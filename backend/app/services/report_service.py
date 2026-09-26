import html as html_module
import logging
import os
import re
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session
from xhtml2pdf import pisa

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.models.project import Project
from app.models.report import Report
from app.models.user import User
from app.services import ai_service, report_context_service
from app.services.ai_service import AIUnavailableError
from app.utils.formatting import format_money

logger = logging.getLogger("report_service")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
_env.filters["money"] = format_money


def _render_ai_markdown(text: str) -> str:
    """Converts the narrow Markdown subset the AI prompt asks for (## headings, - bullet
    lists, plain paragraphs, **bold**) into safe HTML. All text content is HTML-escaped
    FIRST, then the limited formatting is applied on top — the AI's raw output can never
    inject arbitrary HTML/scripts into the report, regardless of what it returns."""
    escaped = html_module.escape(text)
    lines = escaped.split("\n")
    html_parts: list[str] = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line.startswith("## "):
            close_list()
            html_parts.append(f"<h3>{line[3:].strip()}</h3>")
            continue
        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{line[2:].strip()}</li>")
            continue
        close_list()
        html_parts.append(f"<p>{line}</p>")
    close_list()

    joined = "\n".join(html_parts)
    # **bold** -> <strong>bold</strong> (applied after escaping, so this can't reintroduce
    # unescaped HTML from the AI's own output — only wraps already-escaped text segments).
    joined = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", joined)
    return joined


def _extract_ai_recommendations(text: str) -> list[str]:
    """Pulls the bullet points under the AI narrative's own '## Recommendations' heading
    (the prompt in ai_prompts.py always asks for one) — used as a fallback for the
    Executive Summary's 'Top recommendations' list when there's no verified model-driven
    narrative to show instead (e.g. a clustering or general-analysis report, or a
    regression/classification combo build_future_outlook() doesn't have a branch for).
    Returns at most 3 bullets; never invents anything not already in the AI's own text."""
    lines = text.split("\n")
    collecting = False
    bullets: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            collecting = line[3:].strip().lower() == "recommendations"
            continue
        if collecting and (line.startswith("- ") or line.startswith("* ")):
            bullets.append(line[2:].strip())
        if len(bullets) >= 3:
            break
    return bullets


_RAW_PYTHON_DUMP_PATTERNS = (
    re.compile(r"\{'[^']{1,60}':"),  # {'key': ...
    re.compile(r'\{"[^"]{1,60}":\s*[\[\{\'"0-9]'),  # {"key": [ / { / '...' / "..." / 123
    re.compile(r"^\s*\[\{.*\}\]\s*$", re.MULTILINE),  # [{...}] on its own line
)


def _assert_no_raw_python_dumps(html: str) -> None:
    """Best-effort safety net: scans the fully-rendered report HTML for tell-tale
    signatures of an accidentally-rendered raw Python dict/list (e.g. a template change
    that dumps a whole context value with `{{ value }}` instead of iterating it). Logs a
    loud warning rather than failing report generation outright, since a false positive
    here must never block a legitimate report."""
    for pattern in _RAW_PYTHON_DUMP_PATTERNS:
        if pattern.search(html):
            logger.warning(
                "Report HTML may contain a raw Python dict/list dump (pattern %s matched) — "
                "check the template for a value rendered without explicit formatting.",
                pattern.pattern,
            )
            return


def _generate_ai_insights(context: dict) -> dict:
    # Only send the AI a purpose-built, already-summarized slice of the context — never
    # the full report context (which includes base64 chart images and column-by-column
    # dumps) — so the prompt stays small and strictly verified-data-only.
    ai_context: dict = {"project": context["meta"]["project_name"]}
    if context["dataset"]:
        ai_context["dataset"] = {
            "file_name": context["dataset"]["file_name"],
            "rows": context["dataset"]["row_count"],
            "columns": context["dataset"]["column_count"],
        }
    if context["data_quality"]:
        ai_context["data_quality"] = {
            "original_missing_cells": context["data_quality"]["original"]["missing_cells"],
            "original_duplicate_rows": context["data_quality"]["original"]["duplicate_rows"],
            "current_missing_cells": context["data_quality"]["current"]["missing_cells"],
            "current_duplicate_rows": context["data_quality"]["current"]["duplicate_rows"],
        }
    if context["clusters"]:
        cl = context["clusters"]
        ai_context["clusters"] = {
            "method": cl["method_label"],
            "k": cl["k"],
            "silhouette_score": cl["silhouette_score"],
            "features_used": cl["features_used"],
            "cluster_sizes": [{"cluster": p["cluster"], "size": p["size"], "pct": p["pct"]} for p in cl["profiles"]],
        }
    if context["transaction_analysis"]:
        ta = context["transaction_analysis"]
        ai_context["transaction_analysis"] = {}
        if ta.get("revenue"):
            r = ta["revenue"]
            ai_context["transaction_analysis"]["revenue"] = {
                "total_revenue": r["total_revenue"],
                "refund_total": r["refund_total"],
                "refund_rate_pct": r["refund_rate_pct"],
                "average_order_value": r["average_order_value"],
                "top_categories": r["revenue_by_category"][:5],
                "top_branches": r["revenue_by_branch"][:5],
            }
        if ta.get("rfm"):
            ai_context["transaction_analysis"]["customer_segments"] = ta["rfm"]["segments"]
        if ta.get("return_prediction"):
            ai_context["transaction_analysis"]["return_prediction"] = ta["return_prediction"]
        if ta.get("forecast"):
            ai_context["transaction_analysis"]["forecast"] = ta["forecast"]["forecast"]
    if context["model"]:
        m = context["model"]
        ai_context["model"] = {
            "target": m["target_label"],
            "problem_type": m["problem_type"],
            "algorithm": m["model_type_label"] if "model_type_label" in m else m["model_type"],
            "metrics": {k: v for k, v in m["metrics"].items() if isinstance(v, (int, float, str))},
            "is_weak": m["is_weak"],
            "weak_reason": m["weak_reason"],
            "excluded_leakage_features": [w["column"] for w in m["leakage_warnings"]],
        }
    if context["feature_importance"]:
        ai_context["top_features"] = [
            {"feature": f["label"], "importance": f["importance"]} for f in context["feature_importance"][:5]
        ]
    if context["future_outlook"]:
        ai_context["future_outlook_summary"] = context["future_outlook"]["narrative"]

    try:
        text, provider = ai_service.generate_report_insight(ai_context)
        return {
            "available": True,
            "provider": provider,
            "html": _render_ai_markdown(text),
            "unavailable_reason": None,
            "recommendations": _extract_ai_recommendations(text),
        }
    except AIUnavailableError as exc:
        logger.info("AI Business Insights unavailable for this report: %s", exc)
        return {"available": False, "provider": None, "html": None, "unavailable_reason": str(exc), "recommendations": []}


def render_report_html(
    db: Session,
    project: Project,
    dataset: Dataset | None,
    model: MLModel | None,
    title: str | None,
    prepared_by: str | None,
    clusters: dict | None = None,
    no_model_reason: str | None = None,
    transaction_analysis: dict | None = None,
) -> tuple[str, dict]:
    """Builds the verified report context and renders it to HTML — the SAME function and
    the SAME context object are used for both the downloadable PDF and the HTML preview,
    so the two can never show different numbers. `clusters` (from
    ml_service.run_clustering_analysis) is only passed for the no-target 'General
    Analysis' path — mutually exclusive with `model`. `no_model_reason` explains an
    absent model in plain language (e.g. 'no target column was confidently detected')
    instead of the generic default, distinguishing this from a manually-requested
    dataset-only report where no reason is needed. `transaction_analysis` (from
    transaction_analysis_service.run_full_transaction_analysis) is only passed for a
    transaction-log dataset's revenue/RFM/return-prediction/forecast report."""
    context = report_context_service.build_report_context(
        db,
        project,
        dataset,
        model,
        title,
        prepared_by,
        clusters=clusters,
        no_model_reason=no_model_reason,
        transaction_analysis=transaction_analysis,
    )
    context["ai_insights"] = _generate_ai_insights(context)

    template = _env.get_template("report.html")
    html = template.render(**context)
    _assert_no_raw_python_dumps(html)
    return html, context


def generate_report(
    db: Session,
    project: Project,
    dataset: Dataset | None,
    model: MLModel | None,
    title: str | None,
    current_user: User | None = None,
    clusters: dict | None = None,
    no_model_reason: str | None = None,
    transaction_analysis: dict | None = None,
) -> Report:
    prepared_by = current_user.name if current_user else (project.owner.name if project.owner else None)
    html, context = render_report_html(
        db,
        project,
        dataset,
        model,
        title,
        prepared_by,
        clusters=clusters,
        no_model_reason=no_model_reason,
        transaction_analysis=transaction_analysis,
    )

    report_dir = os.path.join(settings.REPORT_DIR, str(project.id))
    os.makedirs(report_dir, exist_ok=True)
    report_id_placeholder = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    pdf_path = os.path.join(report_dir, f"report_{report_id_placeholder}.pdf")
    html_path = os.path.join(report_dir, f"report_{report_id_placeholder}.html")

    with open(pdf_path, "wb") as pdf_file:
        result = pisa.CreatePDF(src=html, dest=pdf_file)
    if result.err:
        logger.warning("xhtml2pdf reported %s error(s) while rendering report for project %s", result.err, project.id)

    # The HTML preview is saved as the EXACT string that produced the PDF above (not
    # re-derived later from live data) — the only way to guarantee the two can never
    # contradict each other, even if the dataset/model is later changed or re-cleaned.
    with open(html_path, "w", encoding="utf-8") as html_file:
        html_file.write(html)

    report = Report(
        project_id=project.id,
        dataset_id=dataset.id if dataset else None,
        model_id=model.id if model else None,
        title=context["meta"]["title"],
        content_json={
            "ai_insight": context["ai_insights"]["html"],
            "ai_available": context["ai_insights"]["available"],
            "ai_provider": context["ai_insights"]["provider"],
            "generated_at": context["meta"]["generated_at"],
        },
        report_path=pdf_path,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def get_report_preview_html(report: Report) -> str:
    """Reads back the exact HTML that produced this report's PDF — never re-derived from
    (possibly since-changed) live data, so the preview and the downloaded PDF can never
    show different numbers for the same report."""
    if not report.report_path:
        raise FileNotFoundError("This report has no stored file.")
    html_path = report.report_path.rsplit(".", 1)[0] + ".html"
    if not os.path.exists(html_path):
        raise FileNotFoundError("This report's HTML preview is not available (it may predate this feature).")
    with open(html_path, "r", encoding="utf-8") as html_file:
        return html_file.read()
