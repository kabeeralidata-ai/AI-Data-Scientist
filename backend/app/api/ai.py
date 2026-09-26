import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.models import get_owned_model
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.ai_conversation import AIConversation
from app.models.ai_insight_cache import AIInsightCache
from app.models.ai_message import AIMessage
from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.ai import (
    AIConversationResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    InsightRequest,
    InsightResponse,
)
from app.services import ai_service
from app.services.context_service import (
    build_cluster_context,
    build_dataset_context,
    build_eda_context,
    build_model_context,
    build_project_context,
    build_transaction_context,
    compute_insight_cache_key,
)
from app.services.dataset_service import find_ungrounded_concepts

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
def ai_status(force: bool = False):
    """force=True (the Settings 'Test Connection' button) always performs a real,
    live Gemini request. Passive polling (navbar, AITab) omits it and gets the
    short-lived cached result instead — see gemini_service.check_connection."""
    return ai_service.check_status(force=force)


def _resolve_insight_targets(db: Session, payload_model_id, payload_dataset_id, current_user: User):
    """Returns (dataset, model, project_id). Both dataset/model may be None, but at
    least one must be provided (checked by the caller)."""
    model = None
    dataset = None
    project_id = None

    if payload_model_id:
        model = get_owned_model(db, payload_model_id, current_user)
        project_id = model.project_id
    if payload_dataset_id:
        dataset = db.query(Dataset).filter(Dataset.id == payload_dataset_id).first()
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found.")
        get_owned_project(db, dataset.project_id, current_user)
        project_id = dataset.project_id

    return dataset, model, project_id


def _cached_insight_row(db: Session, project_id, cache_key: str) -> AIInsightCache | None:
    return (
        db.query(AIInsightCache)
        .filter(AIInsightCache.project_id == project_id, AIInsightCache.cache_key == cache_key)
        .first()
    )


def _latest_completed_job(db: Session, dataset_id) -> AutoAnalyzeJob | None:
    return (
        db.query(AutoAnalyzeJob)
        .filter(AutoAnalyzeJob.dataset_id == dataset_id, AutoAnalyzeJob.status == AutoAnalyzeJobStatus.COMPLETED)
        .order_by(AutoAnalyzeJob.updated_at.desc())
        .first()
    )


def _build_insight_context_and_kind(db: Session, dataset: Dataset | None, model) -> tuple[dict, str]:
    """Builds the same verified context AND the `kind` discriminator
    compute_insight_cache_key needs, for whichever analysis type this dataset/model
    combination actually represents — a trained model, clustering, or a transaction-log
    analysis. Previously this only ever built model/dataset/EDA context, so the AITab's
    Generate/Regenerate button silently produced a generic, cluster-and-revenue-blind
    insight for clustering and transaction-log projects even though the UI offered it."""
    context: dict = {}
    if model:
        context.update(build_model_context(model))
    if dataset:
        context.update(build_dataset_context(dataset))
        context.update(build_eda_context(db, dataset))

    if model:
        return context, "model"

    if dataset:
        job = _latest_completed_job(db, dataset.id)
        result = (job.result_json or {}) if job else {}
        analysis_type = result.get("analysis_type")
        if analysis_type == "transaction_log" and result.get("transaction_analysis"):
            context.update(build_transaction_context(dataset, result["transaction_analysis"]))
            return context, "transaction_log"
        if analysis_type == "clustering" and result.get("clusters"):
            context.update(build_cluster_context(result["clusters"]))
            return context, "clustering"

    return context, "dataset"


@router.get("/insights/cache", response_model=InsightResponse)
def get_cached_insight(
    dataset_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Read-only hydration for page load: returns a previously-generated insight if one
    exists for the CURRENT dataset/model version, WITHOUT ever calling Gemini — safe to
    call automatically on mount/reload/navigation. 404 means 'nothing generated yet',
    not a failure; the frontend should show a neutral empty state, not an error."""
    dataset, model, project_id = _resolve_insight_targets(db, model_id, dataset_id, current_user)
    if not dataset and not model:
        raise HTTPException(status_code=400, detail="Provide a dataset_id or model_id.")

    _, kind = _build_insight_context_and_kind(db, dataset, model)
    cache_key = compute_insight_cache_key(dataset, model, kind)
    cached = _cached_insight_row(db, project_id, cache_key)
    if not cached:
        raise HTTPException(status_code=404, detail="No cached insight for this dataset/model version yet.")

    return InsightResponse(
        insight=cached.insight,
        ai_available=cached.ai_available,
        provider=cached.provider,
        source_context={},
        generated_at=cached.generated_at,
        cached=True,
    )


@router.post("/insights", response_model=InsightResponse)
def generate_insights(payload: InsightRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset, model, project_id = _resolve_insight_targets(db, payload.model_id, payload.dataset_id, current_user)
    if not dataset and not model:
        raise HTTPException(status_code=400, detail="Provide a dataset_id or model_id to generate insights.")

    context, kind = _build_insight_context_and_kind(db, dataset, model)

    cache_key = compute_insight_cache_key(dataset, model, kind)

    if not payload.force:
        cached = _cached_insight_row(db, project_id, cache_key)
        if cached and cached.ai_available:
            # A valid cached SUCCESS for this exact dataset/model version — reuse it
            # rather than spending another Gemini request. A cached FAILURE is never
            # reused here (see below: failures are never persisted in the first place),
            # so a request never gets silently stuck replaying an old error.
            return InsightResponse(
                insight=cached.insight,
                ai_available=cached.ai_available,
                provider=cached.provider,
                source_context=context,
                generated_at=cached.generated_at,
                cached=True,
            )

    provider = None
    try:
        insight, provider = ai_service.generate_insight(context)
        ai_available = True
    except ai_service.AIUnavailableError as exc:
        insight = ai_service.unavailable_message("AI Insights generation", exc.reason)
        ai_available = False

    generated_at = datetime.now(timezone.utc)
    if ai_available:
        existing = _cached_insight_row(db, project_id, cache_key)
        if existing:
            existing.insight = insight
            existing.ai_available = True
            existing.provider = provider
            existing.dataset_id = dataset.id if dataset else None
            existing.model_id = model.id if model else None
        else:
            db.add(
                AIInsightCache(
                    project_id=project_id,
                    dataset_id=dataset.id if dataset else None,
                    model_id=model.id if model else None,
                    cache_key=cache_key,
                    insight=insight,
                    ai_available=True,
                    provider=provider,
                )
            )
        db.commit()

    return InsightResponse(
        insight=insight,
        ai_available=ai_available,
        provider=provider,
        source_context=context,
        generated_at=generated_at,
        cached=False,
    )


@router.post("/chat", response_model=ChatMessageResponse)
def chat(payload: ChatMessageRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = get_owned_project(db, payload.project_id, current_user)

    if payload.conversation_id:
        conversation = (
            db.query(AIConversation)
            .filter(AIConversation.id == payload.conversation_id, AIConversation.project_id == project.id)
            .first()
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    else:
        conversation = AIConversation(project_id=project.id, title=payload.message[:80])
        db.add(conversation)
        db.flush()

    user_message = AIMessage(conversation_id=conversation.id, role="user", content=payload.message)
    db.add(user_message)
    db.flush()

    history = [
        {"role": m.role, "content": m.content}
        for m in sorted(conversation.messages, key=lambda m: m.created_at)
    ]

    context = build_project_context(db, project)

    known_columns = list(context.get("dataset", {}).get("numeric_columns", [])) + list(
        context.get("dataset", {}).get("categorical_columns", [])
    )
    target_column = context.get("target")
    ungrounded = find_ungrounded_concepts(payload.message, known_columns, target_column)
    if ungrounded:
        # The question mentions a concept (e.g. "churn") that doesn't correspond to any
        # real column in this project's data. This is only a HINT for the AI, not a hard
        # block — a general/definitional question ("what is churn?") should still be
        # answered from the AI's own knowledge; a question genuinely asking about THIS
        # dataset should get an honest "this project's data doesn't have that" answer.
        # The system prompt instructs it to tell the two apart.
        context["question_terms_not_found_in_dataset"] = {
            "terms": sorted(ungrounded),
            "available_columns": sorted(set(known_columns))[:20],
        }

    try:
        answer, provider = ai_service.answer_question(context, payload.message, history)
        ai_available = True
        ai_generated = True
    except ai_service.AIUnavailableError as exc:
        answer = ai_service.unavailable_message("AI chat", exc.reason)
        ai_available = False
        ai_generated = False
        provider = None

    assistant_message = AIMessage(
        conversation_id=conversation.id, role="assistant", content=answer, ai_generated=ai_generated
    )
    db.add(assistant_message)
    db.commit()

    return ChatMessageResponse(
        conversation_id=conversation.id,
        answer=answer,
        ai_available=ai_available,
        ai_generated=ai_generated,
        provider=provider,
    )


@router.get("/conversations/{project_id}", response_model=list[AIConversationResponse])
def list_conversations(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, current_user)
    return (
        db.query(AIConversation)
        .filter(AIConversation.project_id == project_id)
        .order_by(AIConversation.created_at.desc())
        .all()
    )
