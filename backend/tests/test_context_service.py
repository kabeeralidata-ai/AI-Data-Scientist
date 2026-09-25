"""Tests for context_service's honesty additions: the AI must be told about the
ORIGINAL (pre-cleaning) data quality, not just the cleaned figures, and must be told
explicitly when a model is weak so it doesn't invent recommendations from it."""

from types import SimpleNamespace

from app.services.context_service import build_dataset_context, build_model_context


def _fake_dataset(**overrides):
    defaults = dict(
        file_name="data.csv",
        row_count=100,
        column_count=5,
        missing_values=0,
        duplicate_rows=0,
        profile_json={"numeric_columns": ["a"], "categorical_columns": ["b"]},
        is_cleaned=True,
        cleaning_log_json={"missing_cells_before": 455, "duplicates_removed": 30},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_dataset_context_includes_original_quality_note_after_cleaning():
    """Regression test: the AI previously said 'zero missing cells' after cleaning,
    which is misleading — the original file had real missing values/duplicates that
    were filled/removed, not absent."""
    dataset = _fake_dataset()
    context = build_dataset_context(dataset)

    assert context["dataset"]["missing_values"] == 0  # current, post-cleaning figure
    note = context["dataset"]["data_quality_note"]
    assert "455" in note
    assert "30" in note
    assert "original" in note.lower()


def test_dataset_context_omits_quality_note_when_never_cleaned():
    dataset = _fake_dataset(is_cleaned=False, cleaning_log_json=None)
    context = build_dataset_context(dataset)
    assert "data_quality_note" not in context["dataset"]


def _fake_model(is_weak, weak_reason=None):
    return SimpleNamespace(
        target_column="late_delivery",
        model_type="gradient_boosting",
        problem_type="classification",
        metrics_json={"f1": 0.5, "roc_auc": 0.51, "is_weak": is_weak, "weak_reason": weak_reason},
        feature_importance_json=[{"name": "a", "importance": 1.0}],
    )


def test_model_context_includes_quality_verdict_when_weak():
    model = _fake_model(True, "ROC-AUC (0.51) is close to 0.5 — not much better than random guessing.")
    context = build_model_context(model)
    assert context["model_quality_verdict"]["is_weak"] is True
    assert "random guessing" in context["model_quality_verdict"]["reason"]


def test_model_context_omits_quality_verdict_when_strong():
    model = _fake_model(False, None)
    context = build_model_context(model)
    assert "model_quality_verdict" not in context
