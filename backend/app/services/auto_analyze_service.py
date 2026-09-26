import copy
import logging

from sqlalchemy.orm.attributes import flag_modified

from app.core.database import SessionLocal
from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus
from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.models.project import Project, ProjectStatus
from app.schemas.dataset import CleaningRequest
from app.services import (
    ai_service,
    cleaning_service,
    data_understanding_service,
    dataset_service,
    eda_service,
    ml_service,
    report_service,
    transaction_analysis_service,
)
from app.services.cleaning_service import flag_invalid_dates
from app.services.context_service import build_dataset_context, build_eda_context, build_model_context
from app.services.customer_analytics_service import pick_column
from app.utils.validators import DatasetValidationError

logger = logging.getLogger("auto_analyze")

STEP_DEFINITIONS = [
    ("profile", "Profile dataset"),
    ("data_understanding", "Understand columns & dataset type"),
    ("cleaning", "Recommended cleaning"),
    ("eda", "Exploratory data analysis"),
    ("target_detection", "Target detection"),
    ("training", "Train suitable models"),
    ("comparison", "Compare & select best model"),
    ("ai_insights", "AI insights"),
    ("report", "Generate report"),
]


def _initial_steps() -> list[dict]:
    return [{"key": k, "label": label, "status": "pending", "detail": None} for k, label in STEP_DEFINITIONS]


def create_job(db, project_id, dataset_id, force_target_reselection: bool = False) -> AutoAnalyzeJob:
    job = AutoAnalyzeJob(
        project_id=project_id,
        dataset_id=dataset_id,
        status=AutoAnalyzeJobStatus.PENDING,
        steps_json=_initial_steps(),
        force_target_reselection=force_target_reselection,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _has_strong_model_for_target(db, dataset_id, target_column: str) -> bool:
    """A remembered target is only trustworthy to silently reuse if at least one
    already-trained model for it wasn't flagged weak — otherwise the only evidence for
    that target is a model that doesn't really predict anything (e.g. a grouping column
    auto-picked before scoring existed), and it should be re-confirmed instead."""
    models = (
        db.query(MLModel)
        .filter(MLModel.dataset_id == dataset_id, MLModel.target_column == target_column)
        .all()
    )
    return any(not (m.metrics_json or {}).get("is_weak", False) for m in models)


def _set_step(job: AutoAnalyzeJob, key: str, status: str, detail: str | None = None) -> None:
    # SQLAlchemy's plain JSON column type doesn't detect in-place mutation of the Python
    # list it hands back, even when reassigned — deep-copy to a genuinely new object and
    # flag it modified explicitly so every step update is actually persisted.
    steps = copy.deepcopy(job.steps_json) if job.steps_json else _initial_steps()
    for step in steps:
        if step["key"] == key:
            step["status"] = status
            if detail is not None:
                step["detail"] = detail
            break
    job.steps_json = steps
    flag_modified(job, "steps_json")
    job.current_step = key


def run_auto_analyze(job_id) -> None:
    """Entry point for a freshly created job: Profile -> Cleaning -> EDA -> Target
    Detection, then either pauses for the user to confirm the target (first run for this
    project) or continues straight through training if a target was already confirmed for
    this project on a previous run (the choice is "remembered" via project.target_column).
    Executed as a FastAPI BackgroundTask with its own DB session since it outlives the
    original request."""
    db = SessionLocal()
    try:
        job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
        if not job:
            return

        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        project = db.query(Project).filter(Project.id == job.project_id).first()

        job.status = AutoAnalyzeJobStatus.RUNNING
        project.status = ProjectStatus.ANALYZING
        db.commit()

        try:
            target_column, dataset = _run_profile_through_target_detection(db, job, dataset, project)
        except DatasetValidationError as exc:
            _fail_job(db, job, project, str(exc))
            return
        except Exception:
            logger.exception("Auto Analyze job %s failed during profiling/target detection", job_id)
            _fail_job(db, job, project, "An unexpected error occurred during automated analysis.")
            return

        if target_column is None:
            # Paused: job.status is AWAITING_TARGET_CONFIRMATION, waiting on a separate
            # confirm-target request from the user.
            return

        _continue_training(db, job, dataset, project, target_column)
    finally:
        db.close()


def confirm_target_and_resume(job_id, target_column: str) -> None:
    """Resumes a paused job once the user has confirmed (or overridden) the suggested
    target column, then remembers the choice on the project so future Auto Analyze runs
    on this project skip the pause."""
    db = SessionLocal()
    try:
        job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
        if not job or job.status != AutoAnalyzeJobStatus.AWAITING_TARGET_CONFIRMATION:
            return

        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        project = db.query(Project).filter(Project.id == job.project_id).first()

        job.confirmed_target_column = target_column
        job.status = AutoAnalyzeJobStatus.RUNNING
        project.status = ProjectStatus.ANALYZING
        _set_step(job, "target_detection", "completed", f"Confirmed target: '{target_column}'")
        db.commit()

        _continue_training(db, job, dataset, project, target_column)
    finally:
        db.close()


def confirm_plan_and_resume(job_id) -> None:
    """Resumes a job paused at AWAITING_PLAN_CONFIRMATION (a transaction-log dataset) —
    the user has reviewed build_analysis_plan()'s output and asked to proceed. Runs
    exactly the analyses the plan marked viable; nothing here is re-derived from scratch,
    reusing the column_roles/plan already computed and stored on the job."""
    db = SessionLocal()
    try:
        job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
        if not job or job.status != AutoAnalyzeJobStatus.AWAITING_PLAN_CONFIRMATION:
            return

        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        project = db.query(Project).filter(Project.id == job.project_id).first()

        job.status = AutoAnalyzeJobStatus.RUNNING
        project.status = ProjectStatus.ANALYZING
        db.commit()

        _run_transaction_log_analysis(db, job, dataset, project)
    finally:
        db.close()


def retry_ai_insights(job_id) -> None:
    """Re-attempts just the AI insights step of an already-completed job without
    re-running the rest of the pipeline — backs the Retry button shown when Ollama was
    unavailable during the original run."""
    db = SessionLocal()
    try:
        job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
        if not job or not job.result_json or not job.result_json.get("best_model_id"):
            return

        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        best_model = db.query(MLModel).filter(MLModel.id == job.result_json["best_model_id"]).first()
        if not dataset or not best_model:
            return

        _set_step(job, "ai_insights", "running")
        db.commit()

        context: dict = {}
        context.update(build_dataset_context(dataset))
        context.update(build_eda_context(db, dataset))
        context.update(build_model_context(best_model))

        result = copy.deepcopy(job.result_json)
        try:
            insight, provider = ai_service.generate_insight(context)
            result["ai_insight"] = insight
            result["ai_available"] = True
            result["ai_provider"] = provider
            result["ai_reason"] = None
            _set_step(job, "ai_insights", "completed", f"AI narrative generated ({provider}).")
        except ai_service.AIUnavailableError as exc:
            result["ai_insight"] = None
            result["ai_available"] = False
            result["ai_reason"] = exc.reason
            _set_step(job, "ai_insights", "warning", ai_service.unavailable_message("AI narrative", exc.reason))

        job.result_json = result
        flag_modified(job, "result_json")
        db.commit()
    finally:
        db.close()


def _fail_job(db, job: AutoAnalyzeJob, project: Project, message: str) -> None:
    if job.current_step:
        _set_step(job, job.current_step, "failed", message)
    job.status = AutoAnalyzeJobStatus.FAILED
    job.error_message = message
    project.status = ProjectStatus.FAILED
    db.commit()


def _run_profile_through_target_detection(db, job: AutoAnalyzeJob, dataset: Dataset, project: Project):
    """Runs steps 1-4 (Profile, Cleaning, EDA, Target Detection). Returns
    (target_column, dataset) if the pipeline can proceed immediately, or (None, dataset)
    if the job has been paused awaiting the user's target confirmation."""
    # 1. Profile — read the untouched original upload rather than dataset.row_count, which
    # gets overwritten to the CLEANED count once cleaning runs; on a repeat Auto Analyze
    # run over an already-cleaned dataset, dataset.row_count would otherwise silently
    # report the previous clean's row count instead of the file's true original size.
    _set_step(job, "profile", "running")
    db.commit()
    if not dataset.profile_json:
        raise DatasetValidationError("This dataset has not been profiled yet.")
    original_df = dataset_service.load_original_dataframe(dataset)
    original_row_count = len(original_df)
    original_column_count = len(original_df.columns)
    _set_step(job, "profile", "completed", f"{original_row_count} rows, {original_column_count} columns")
    db.commit()

    # 1.5 Data Understanding — column roles + dataset type + formula columns, computed
    # from the ORIGINAL (pre-cleaning) data so cleaning decisions below can be informed by
    # what each column actually represents (e.g. never treat a refund as a data-entry
    # error, never fill a missing customer ID with someone else's ID).
    _set_step(job, "data_understanding", "running")
    db.commit()
    understanding = data_understanding_service.understand_dataset(dataset, use_gemini=True)
    column_roles = understanding["column_roles"]
    dataset_type_info = understanding["dataset_type"]
    formula_columns = understanding["formula_columns"]
    _set_step(
        job,
        "data_understanding",
        "completed",
        f"Dataset type: {dataset_type_info['type']} ({dataset_type_info['reason']})."
        + (f" Formula column(s) detected: {', '.join(f['column'] for f in formula_columns)}." if formula_columns else ""),
    )
    db.commit()

    # 2. Recommended cleaning
    _set_step(job, "cleaning", "running")
    db.commit()
    dataset = cleaning_service.clean_dataset(
        db, dataset, CleaningRequest(missing_strategy="auto", remove_duplicates=True), column_roles=column_roles
    )
    log = dataset.cleaning_log_json or {}
    _set_step(
        job,
        "cleaning",
        "completed",
        f"{original_row_count} rows → {dataset.row_count} rows after cleaning "
        f"(removed {log.get('duplicates_removed', 0)} duplicate row(s), "
        f"filled {log.get('missing_cells_before', 0)} missing cell(s)).",
    )
    db.commit()

    # 2.5 Transaction-log datasets branch into their own planner + pipeline (revenue
    # analytics, customer/RFM, return prediction, forecasting) instead of the generic
    # target-detection/training flow below — spec: "show the plan to the user to confirm
    # before running." The cleaned dataframe is re-profiled here since cleaning may have
    # changed row/column shape (units stripped, walk-in labels applied, etc.).
    if dataset_type_info["type"] == "transaction_log":
        cleaned_df = dataset_service.load_dataframe(dataset)
        plan = transaction_analysis_service.build_analysis_plan(column_roles, dataset_type_info, cleaned_df)
        result = dict(job.result_json or {})
        result["dataset_type"] = dataset_type_info
        result["formula_columns"] = formula_columns
        result["column_roles"] = column_roles
        result["plan"] = plan
        job.result_json = result
        flag_modified(job, "result_json")
        job.status = AutoAnalyzeJobStatus.AWAITING_PLAN_CONFIRMATION
        _set_step(
            job,
            "eda",
            "skipped",
            "Skipped generic EDA — a transaction-log-specific analysis plan is awaiting confirmation instead.",
        )
        db.commit()
        return None, dataset

    # 3. EDA
    _set_step(job, "eda", "running")
    db.commit()
    eda_result = eda_service.run_eda(db, dataset)
    _set_step(
        job,
        "eda",
        "completed",
        f"{len(eda_result.get('distributions', {}))} numeric column(s) analyzed.",
    )
    db.commit()

    # 4. Target detection — always compute + store the top candidates for transparency,
    # but only pause for confirmation the first time; a project with an already-confirmed
    # target (from a previous Auto Analyze run) reuses it automatically — UNLESS the user
    # explicitly asked to change it ("Change target"), or the only evidence for that
    # target is a weak model (e.g. a grouping column auto-picked before scoring existed,
    # which would otherwise get silently reused forever).
    _set_step(job, "target_detection", "running")
    db.commit()
    candidates = dataset_service.score_target_candidates(dataset.profile_json, project.description, formula_columns=formula_columns)
    job.target_candidates_json = candidates
    flag_modified(job, "target_candidates_json")
    db.commit()

    if not candidates:
        # Spec requirement: "If no target exists, allow analysis without supervised ML" —
        # the pipeline must not force every dataset into classification/regression. Falls
        # through to unsupervised clustering (or an EDA-only summary if even that isn't
        # viable) instead of failing the job outright.
        _set_step(
            job,
            "target_detection",
            "completed",
            "No confident target column found — proceeding with unsupervised analysis instead.",
        )
        db.commit()
        _run_general_analysis(db, job, dataset, project, column_roles)
        return None, dataset

    dataset_column_names = {c["name"] for c in (dataset.profile_json or {}).get("columns", [])}
    remembered = project.target_column
    if (
        not job.force_target_reselection
        and remembered
        and remembered in dataset_column_names
        and _has_strong_model_for_target(db, dataset.id, remembered)
    ):
        job.confirmed_target_column = remembered
        _set_step(job, "target_detection", "completed", f"Using previously confirmed target: '{remembered}'")
        db.commit()
        return remembered, dataset

    job.status = AutoAnalyzeJobStatus.AWAITING_TARGET_CONFIRMATION
    top = candidates[0]
    _set_step(job, "target_detection", "completed", f"Suggested target: '{top['column']}' — awaiting confirmation")
    db.commit()
    return None, dataset


def _continue_training(db, job: AutoAnalyzeJob, dataset: Dataset, project: Project, target_column: str) -> None:
    """Runs steps 5-8 (Train, Compare, AI insights, Report) against a confirmed target."""
    result: dict = dict(job.result_json or {})
    result["target_column"] = target_column
    result["row_count"] = dataset.row_count
    result["column_count"] = dataset.column_count

    try:
        # 5. Train suitable models (auto feature selection — ID/date exclusion, date
        # feature extraction, leakage detection — all candidate models for the detected
        # problem type, default test size / CV folds)
        _set_step(job, "training", "running")
        db.commit()
        trained_models = ml_service.train_models(
            db,
            project.id,
            dataset,
            target_column,
            feature_columns=None,
            test_size=0.2,
            random_seed=42,
            model_keys=None,
            cv_folds=5,
        )
        if not trained_models:
            raise DatasetValidationError("No model could be trained on this dataset.")

        leakage_warnings = trained_models[0].metrics_json.get("leakage_warnings") or []
        post_outcome_excluded = trained_models[0].metrics_json.get("post_outcome_excluded") or []
        preprocessing = trained_models[0].preprocessing_json or {}
        result["leakage_warnings"] = leakage_warnings
        result["post_outcome_excluded"] = post_outcome_excluded
        result["excluded_id_like"] = preprocessing.get("excluded_id_like", [])
        result["excluded_datetime_raw"] = preprocessing.get("excluded_datetime_raw", [])
        result["derived_date_features"] = preprocessing.get("derived_date_features", [])

        # Excluding a leaked or post-outcome-suspicious feature is the pipeline doing its
        # job correctly, not a problem — the training step stays green/"completed" either
        # way, with the exclusions reported informationally in the detail text. "warning"
        # is reserved for something that genuinely still needs the user's attention (a
        # weak model vs. baseline, AI unavailable), not a successful automatic exclusion.
        training_detail = f"Trained {len(trained_models)} model(s)."
        if leakage_warnings:
            leaked_names = ", ".join(w["column"] for w in leakage_warnings)
            training_detail += f" Excluded {len(leakage_warnings)} leaked feature(s): {leaked_names}."
        if post_outcome_excluded:
            excluded_names = ", ".join(w["column"] for w in post_outcome_excluded)
            training_detail += (
                f" Excluded {len(post_outcome_excluded)} post-outcome feature(s) by default: "
                f"{excluded_names} (re-include from the Modeling tab if known in advance)."
            )
        _set_step(job, "training", "completed", training_detail)
        db.commit()

        # 6. Compare & select best (already flagged by train_models via is_best)
        _set_step(job, "comparison", "running")
        db.commit()
        best_model = next((m for m in trained_models if m.is_best), trained_models[0])
        result["best_model_id"] = str(best_model.id)
        result["best_model_type"] = best_model.model_type
        result["problem_type"] = best_model.problem_type
        primary_metric = "f1" if best_model.problem_type == "classification" else "r2"
        result["best_model_score"] = (best_model.metrics_json or {}).get(primary_metric)
        baseline = (best_model.metrics_json or {}).get("baseline") or {}
        result["baseline"] = baseline
        is_weak = bool((best_model.metrics_json or {}).get("is_weak"))
        result["is_weak_model"] = is_weak
        result["weak_model_reason"] = (best_model.metrics_json or {}).get("weak_reason")

        comparison_detail = f"Best model: {best_model.model_type} ({primary_metric}={result['best_model_score']})"
        comparison_status = "completed"
        if is_weak:
            comparison_status = "warning"
            comparison_detail = (
                "This model is not much better than random guessing. The selected target "
                "may not be predictable from these features. "
                f"({comparison_detail}; {result['weak_model_reason']})"
            )
        _set_step(job, "comparison", comparison_status, comparison_detail)
        db.commit()

        project.target_column = target_column
        project.status = ProjectStatus.COMPLETED
        db.commit()

        # 7. AI insights (gracefully degrades if no configured AI provider is reachable —
        # a real issue, so the step is marked "warning", not a silent "completed")
        _set_step(job, "ai_insights", "running")
        db.commit()
        context: dict = {}
        context.update(build_dataset_context(dataset))
        context.update(build_eda_context(db, dataset))
        context.update(build_model_context(best_model))
        try:
            insight, provider = ai_service.generate_insight(context)
            result["ai_insight"] = insight
            result["ai_available"] = True
            result["ai_provider"] = provider
            result["ai_reason"] = None
            _set_step(job, "ai_insights", "completed", f"AI narrative generated ({provider}).")
        except ai_service.AIUnavailableError as exc:
            result["ai_insight"] = None
            result["ai_available"] = False
            result["ai_reason"] = exc.reason
            _set_step(
                job,
                "ai_insights",
                "warning",
                ai_service.unavailable_message("AI narrative", exc.reason)
                + " Analytical results above remain valid.",
            )
        db.commit()

        # 8. Report
        _set_step(job, "report", "running")
        db.commit()
        report = report_service.generate_report(
            db, project, dataset, best_model, title=f"{project.name} — Auto Analysis"
        )
        result["report_id"] = str(report.id)
        _set_step(job, "report", "completed", "Report generated.")

        job.status = AutoAnalyzeJobStatus.COMPLETED
        job.result_json = result
        flag_modified(job, "result_json")
        db.commit()

    except DatasetValidationError as exc:
        if job.current_step:
            _set_step(job, job.current_step, "failed", str(exc))
        job.status = AutoAnalyzeJobStatus.FAILED
        job.error_message = str(exc)
        job.result_json = result
        flag_modified(job, "result_json")
        project.status = ProjectStatus.FAILED
        db.commit()
    except Exception:  # pragma: no cover - defensive catch-all for background task
        logger.exception("Auto Analyze job %s failed unexpectedly", job.id)
        if job.current_step:
            _set_step(job, job.current_step, "failed", "An unexpected error occurred.")
        job.status = AutoAnalyzeJobStatus.FAILED
        job.error_message = "An unexpected error occurred during Auto Analyze."
        job.result_json = result
        flag_modified(job, "result_json")
        project.status = ProjectStatus.FAILED
        db.commit()


def _run_transaction_log_analysis(db, job: AutoAnalyzeJob, dataset: Dataset, project: Project) -> None:
    """Executes the confirmed plan for a transaction-log dataset: revenue analytics,
    customer/RFM segmentation, return prediction, and sales forecasting (whichever the
    plan marked viable) — then one consolidated AI narrative (a single Gemini call, per
    the spec, to avoid burning rate-limit quota on multiple smaller calls) and a report
    with a dedicated Revenue/RFM/Returns/Forecast section."""
    result: dict = dict(job.result_json or {})
    column_roles = result.get("column_roles") or {}
    plan = result.get("plan") or {"analyses": []}

    try:
        _set_step(job, "training", "running")
        db.commit()
        df = dataset_service.load_dataframe(dataset)
        timestamp_col = pick_column(column_roles, "timestamp")
        if timestamp_col:
            invalid_mask = flag_invalid_dates(df[timestamp_col])
            excluded_count = int(invalid_mask.sum())
            df = df[~invalid_mask].copy()
        else:
            excluded_count = 0

        analysis = transaction_analysis_service.run_full_transaction_analysis(df, column_roles, plan)
        result["transaction_analysis"] = analysis
        result["invalid_dates_excluded"] = excluded_count
        result["target_column"] = None
        result["best_model_id"] = None
        result["analysis_type"] = "transaction_log"

        ran = [a["key"] for a in plan["analyses"] if a["viable"]]
        skipped = [a["label"] for a in plan["analyses"] if not a["viable"]]
        detail = f"Ran: {', '.join(ran) if ran else 'none'}."
        if skipped:
            detail += f" Not viable for this dataset: {', '.join(skipped)}."
        if excluded_count:
            detail += f" Excluded {excluded_count} row(s) with invalid/implausible date(s) from time-based analysis."
        _set_step(job, "training", "completed", detail)
        db.commit()

        return_prediction = analysis.get("return_prediction")
        comparison_detail = "Revenue analytics, customer segmentation, and forecasting complete."
        if return_prediction:
            comparison_detail += (
                f" {return_prediction['predicted_will_return']} of {return_prediction['total_customers']} "
                f"customers predicted to return within {return_prediction['window_days']} days "
                f"(holdout accuracy {round(return_prediction['holdout_metrics'].get('accuracy', 0) * 100, 1)}%)."
            )
        _set_step(job, "comparison", "completed", comparison_detail)
        db.commit()

        project.status = ProjectStatus.COMPLETED
        db.commit()

        # AI insights — ONE consolidated call for the whole narrative (spec: avoid
        # multiple Gemini calls / rate limits) rather than one call per analysis.
        _set_step(job, "ai_insights", "running")
        db.commit()
        ai_context: dict = {}
        ai_context.update(build_dataset_context(dataset))
        if analysis.get("revenue_analytics"):
            ai_context["revenue_analytics"] = analysis["revenue_analytics"]
        if analysis.get("customer_rfm"):
            ai_context["customer_segments"] = analysis["customer_rfm"]["segments"]
        if analysis.get("return_prediction"):
            ai_context["return_prediction"] = {
                k: v for k, v in analysis["return_prediction"].items() if k != "predictions"
            }
        if analysis.get("sales_forecasting"):
            ai_context["sales_forecast"] = analysis["sales_forecasting"]
        try:
            insight, provider = ai_service.generate_insight(ai_context)
            result["ai_insight"] = insight
            result["ai_available"] = True
            result["ai_provider"] = provider
            result["ai_reason"] = None
            _set_step(job, "ai_insights", "completed", f"AI narrative generated in a single call ({provider}).")
        except ai_service.AIUnavailableError as exc:
            result["ai_insight"] = None
            result["ai_available"] = False
            result["ai_reason"] = exc.reason
            _set_step(job, "ai_insights", "warning", ai_service.unavailable_message("AI narrative", exc.reason))
        db.commit()

        # Report
        _set_step(job, "report", "running")
        db.commit()
        report = report_service.generate_report(
            db,
            project,
            dataset,
            None,
            title=f"{project.name} — Transaction Analysis",
            transaction_analysis=analysis,
        )
        result["report_id"] = str(report.id)
        _set_step(job, "report", "completed", "Report generated.")

        job.status = AutoAnalyzeJobStatus.COMPLETED
        job.result_json = result
        flag_modified(job, "result_json")
        db.commit()

    except Exception:
        logger.exception("Auto Analyze job %s failed during transaction-log analysis", job.id)
        if job.current_step:
            _set_step(job, job.current_step, "failed", "An unexpected error occurred during transaction-log analysis.")
        job.status = AutoAnalyzeJobStatus.FAILED
        job.error_message = "An unexpected error occurred during transaction-log analysis."
        job.result_json = result
        flag_modified(job, "result_json")
        project.status = ProjectStatus.FAILED
        db.commit()


def _run_general_analysis(
    db, job: AutoAnalyzeJob, dataset: Dataset, project: Project, column_roles: dict | None = None
) -> None:
    """Runs steps 5-8 for a dataset where no target column could be confidently detected —
    the unsupervised counterpart to _continue_training. Tries K-Means clustering on the
    dataset's behavioral/usage columns first (ml_service.run_clustering_analysis); if
    that isn't viable either (too few numeric columns / too few rows), falls back further
    to an EDA-only summary — dataset profile, data quality, and EDA findings are always
    real regardless, so there is always something honest to show, never a fake result."""
    result: dict = dict(job.result_json or {})
    result["target_column"] = None
    result["best_model_id"] = None
    result["row_count"] = dataset.row_count
    result["column_count"] = dataset.column_count

    try:
        _set_step(job, "training", "running")
        db.commit()
        df = dataset_service.load_dataframe(dataset)
        clusters = ml_service.run_clustering_analysis(dataset, df, column_roles=column_roles)

        if clusters:
            result["analysis_type"] = "clustering"
            result["clusters"] = clusters
            _set_step(
                job,
                "training",
                "completed",
                f"Found {clusters['k']} cluster(s) via K-Means across {clusters['rows_clustered']} row(s) "
                f"(silhouette score {clusters['silhouette_score']}).",
            )
            db.commit()
            _set_step(
                job,
                "comparison",
                "completed",
                f"{clusters['k']} cluster(s) selected as the best silhouette-scored grouping.",
            )
        else:
            result["analysis_type"] = "general"
            result["clusters"] = None
            _set_step(
                job,
                "training",
                "skipped",
                "Not enough numeric structure for clustering — showing exploratory findings only.",
            )
            db.commit()
            _set_step(job, "comparison", "skipped", "No model or clustering was applicable to this dataset.")
        db.commit()

        project.status = ProjectStatus.COMPLETED
        db.commit()

        # AI insights — same verified-context principle as the supervised path, just built
        # from dataset/EDA/cluster findings instead of a trained model.
        _set_step(job, "ai_insights", "running")
        db.commit()
        context: dict = {}
        context.update(build_dataset_context(dataset))
        context.update(build_eda_context(db, dataset))
        if clusters:
            context["clusters"] = clusters
        try:
            insight, provider = ai_service.generate_insight(context)
            result["ai_insight"] = insight
            result["ai_available"] = True
            result["ai_provider"] = provider
            result["ai_reason"] = None
            _set_step(job, "ai_insights", "completed", f"AI narrative generated ({provider}).")
        except ai_service.AIUnavailableError as exc:
            result["ai_insight"] = None
            result["ai_available"] = False
            result["ai_reason"] = exc.reason
            _set_step(
                job,
                "ai_insights",
                "warning",
                ai_service.unavailable_message("AI narrative", exc.reason) + " Analytical results above remain valid.",
            )
        db.commit()

        # Report — model=None, clusters=clusters; report_context_service already renders a
        # dataset/quality/EDA-only report with an added Cluster Analysis section when
        # clusters is provided, and gracefully omits model-specific sections entirely.
        _set_step(job, "report", "running")
        db.commit()
        report = report_service.generate_report(
            db,
            project,
            dataset,
            None,
            title=f"{project.name} — General Analysis",
            clusters=clusters,
            no_model_reason="No target column was confidently detected in this dataset, so no supervised model was trained.",
        )
        result["report_id"] = str(report.id)
        _set_step(job, "report", "completed", "Report generated.")

        job.status = AutoAnalyzeJobStatus.COMPLETED
        job.result_json = result
        flag_modified(job, "result_json")
        db.commit()

    except Exception:
        logger.exception("Auto Analyze job %s failed during general/unsupervised analysis", job.id)
        if job.current_step:
            _set_step(job, job.current_step, "failed", "An unexpected error occurred during unsupervised analysis.")
        job.status = AutoAnalyzeJobStatus.FAILED
        job.error_message = "An unexpected error occurred during unsupervised analysis."
        job.result_json = result
        flag_modified(job, "result_json")
        project.status = ProjectStatus.FAILED
        db.commit()
