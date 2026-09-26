import os
import re

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_column import DatasetColumn
from app.utils.file_utils import generate_storage_path, read_dataset_file
from app.utils.validators import (
    DatasetValidationError,
    validate_dataframe_structure,
    validate_file_extension,
    validate_file_size,
)

DATE_HINT_KEYWORDS = ("date", "time", "day", "month", "year", "timestamp")
ID_NAME_PATTERN = re.compile(r"(^id$|_id$|^id_|uuid|guid|^index$|^row.?number$)", re.IGNORECASE)
TARGET_NAME_HINTS = (
    "target", "label", "class", "outcome", "churn", "y", "result",
    "default", "fraud", "response", "purchase", "converted", "success",
)

# Target-scoring vocabulary (see score_target_candidates). Tiered so a column literally
# named "target"/"label" always wins over a generic business-outcome word, and a core
# revenue term (sales/revenue/amount) outranks a secondary financial term (e.g. profit) —
# both are legitimate targets, but the former is the more common intended one.
TARGET_HINT_TIER1 = ("target", "label", "y")
TARGET_HINT_TIER2 = ("sales", "revenue", "amount")
TARGET_HINT_TIER3 = ("price", "profit", "churn", "returned", "outcome", "late", "delayed", "overdue")
GROUPING_NAME_HINTS = ("city", "region", "category", "name", "segment", "channel")


def tokenize_column_name(name: str) -> list[str]:
    """Splits a snake_case/kebab-case/camelCase column name into lowercase word tokens,
    e.g. 'store_city' -> ['store', 'city'], 'salesAmount' -> ['sales', 'amount']."""
    s = re.sub(r"[-\s]+", "_", str(name))
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return [t.lower() for t in s.split("_") if t]


def normalize_token(token: str) -> str:
    """Light stemming (returns/returned -> return) so name-hint matching isn't fooled by
    plural/tense variants without needing a full NLP stemmer."""
    t = token.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if t.endswith(suffix) and len(t) > len(suffix) + 2:
            return t[: -len(suffix)]
    return t


def tokens_match_any(tokens: list[str], hints: tuple[str, ...]) -> bool:
    normalized = {normalize_token(t) for t in tokens}
    hint_normalized = {normalize_token(h) for h in hints}
    return bool(normalized & hint_normalized)


def detect_semantic_type(series: pd.Series, name: str) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    lowered = name.lower()
    if any(k in lowered for k in DATE_HINT_KEYWORDS):
        try:
            parsed = pd.to_datetime(series.dropna().head(50), errors="raise")
            if len(parsed) > 0:
                return "datetime"
        except Exception:
            pass
    nunique = series.nunique(dropna=True)
    if nunique <= max(20, int(0.05 * len(series))):
        return "categorical"
    return "text"


def is_id_like_column(name: str, unique_count: int, row_count: int, is_numeric: bool) -> bool:
    if row_count == 0:
        return False
    if ID_NAME_PATTERN.search(name):
        return True
    # A near-100%-unique column behaves like an identifier rather than a predictive
    # feature — but only for text/categorical columns. Continuous numeric measurements
    # (price, age in days, a precise score, ...) are ALSO almost always unique per row;
    # that's normal, valuable signal, not an ID, so this fallback must not apply to them.
    # Numeric ID columns still get caught above by name (customer_id, order_id, ...).
    if not is_numeric and row_count >= 10 and unique_count >= row_count * 0.98:
        return True
    return False


def score_target_candidates(
    profile: dict, description: str | None = None, formula_columns: list[dict] | None = None
) -> list[dict]:
    """Scores every plausible target column instead of picking the first name-hint match,
    so a grouping/descriptive column (store_city, product_category, ...) never outranks a
    real business outcome (sales_amount, profit, churn, ...) just because it happens to be
    low-cardinality. Returns the top 3 candidates, most plausible first, each with a short
    human-readable reason — surfaced in the UI for the user to confirm or override, never
    silently auto-applied.

    `formula_columns` (from data_understanding_service.detect_formula_columns — a REAL,
    numerically-verified relationship like total = quantity * price - discount, never a
    name guess) excludes a detected formula column from candidacy entirely: predicting a
    value that's an exact arithmetic function of other columns in the same row is a
    degenerate, useless "prediction," not a real target.
    """
    formula_column_names = {f["column"] for f in (formula_columns or [])}
    candidates = [
        c
        for c in profile["columns"]
        if not c.get("is_id_like") and not c["is_datetime"] and c["name"] not in formula_column_names
    ]
    if not candidates:
        return []

    description_lower = (description or "").lower()
    row_count = profile.get("row_count", 0) or 0

    scored = []
    for col in candidates:
        name = col["name"]
        tokens = tokenize_column_name(name)
        reasons: list[str] = []
        score = 0.0
        # A column only becomes a target CANDIDATE at all if something ties it to a
        # business outcome (its name, or the project description) — never from shape
        # alone. Cardinality/type (numeric-with-many-distinct-values, binary, ...) is
        # true of most measurement columns in any dataset (e.g. every usage-metric
        # column in a telecom table), so treating it as sufficient on its own means a
        # target is "found" in literally every dataset, which is exactly root cause #3
        # ("a prediction target always forced") this scorer must not reproduce. Below,
        # cardinality only adjusts the score of an already-qualified candidate.
        qualifies = False

        if tokens_match_any(tokens, TARGET_HINT_TIER1):
            score += 5
            reasons.append("column name explicitly suggests a prediction target")
            qualifies = True
        elif tokens_match_any(tokens, TARGET_HINT_TIER2):
            score += 4
            reasons.append("column name suggests a core business outcome (sales/revenue/amount)")
            qualifies = True
        elif tokens_match_any(tokens, TARGET_HINT_TIER3):
            score += 3
            reasons.append("column name suggests a business outcome")
            qualifies = True

        if tokens_match_any(tokens, GROUPING_NAME_HINTS):
            score -= 3
            reasons.append("column name looks like a grouping/descriptive field, not an outcome")

        unique = col["unique_count"]
        is_numeric = col["is_numeric"]
        if is_numeric and row_count and unique >= max(10, int(0.05 * row_count)):
            score += 2
            reasons.append("numeric with many distinct values (a regression candidate)")
        elif unique == 2:
            score += 2
            reasons.append("binary column (a classification candidate)")
        elif 2 < unique <= 10:
            score += 1
            reasons.append("low-cardinality column (a classification candidate)")
        elif not is_numeric and row_count and unique >= row_count * 0.3:
            score -= 2
            reasons.append("high-cardinality text — likely descriptive, not an outcome")

        normalized_name = name.replace("_", " ").lower()
        if description_lower and normalized_name in description_lower:
            score += 2
            reasons.append("mentioned in the project description")
            qualifies = True

        if qualifies and score > 0:
            problem_type_hint = "regression" if (is_numeric and unique > 10) else "classification"
            scored.append(
                {
                    "column": name,
                    "score": round(score, 2),
                    "reason": "; ".join(reasons) if reasons else "plausible outcome column",
                    "problem_type_hint": problem_type_hint,
                }
            )

    scored.sort(key=lambda c: (c["score"], c["column"]), reverse=True)
    return scored[:3]


def suggest_target_column(profile: dict, description: str | None = None) -> str | None:
    """Heuristic suggestion only (surfaced as a UI default, never auto-applied silently)."""
    candidates = score_target_candidates(profile, description)
    if candidates:
        return candidates[0]["column"]

    # No column scored positively on the name/cardinality heuristics above (e.g. a dataset
    # with no recognizable outcome-style names) — fall back to the weakest possible signal
    # (a plausible low-cardinality column) rather than returning nothing.
    fallback = [c for c in profile["columns"] if not c.get("is_id_like") and not c["is_datetime"]]
    if not fallback:
        return None
    low_cardinality = [c for c in fallback if 1 < c["unique_count"] <= 10]
    if low_cardinality:
        return sorted(low_cardinality, key=lambda c: c["unique_count"])[0]["name"]
    return fallback[-1]["name"]


# Domain-outcome vocabulary the AI chat checks a question against to decide whether it's
# asking about a concept this project's data can actually speak to (see
# find_ungrounded_concepts). Deliberately the same family of words as the target-scoring
# hints above, since both are "does this project have an outcome column matching this
# word" questions.
CHAT_CONCEPT_VOCABULARY = {
    "churn", "fraud", "default", "conversion", "converted", "revenue", "sales", "profit",
    "return", "returned", "outcome", "rating", "segment", "region", "category", "channel",
    "price", "amount", "label", "target", "discount", "promotion",
}


def find_ungrounded_concepts(question: str, known_columns: list[str], target_column: str | None = None) -> list[str]:
    """Flags domain-outcome words in a chat question (e.g. 'churn') that don't correspond
    to any real column in this project's dataset, so the AI chat can say so honestly
    instead of the LLM guessing an answer about a field that doesn't exist (e.g. asking
    about churn in a retail-sales project that has no churn column)."""
    known_tokens: set[str] = set()
    for col in list(known_columns) + ([target_column] if target_column else []):
        known_tokens.update(normalize_token(t) for t in tokenize_column_name(col))

    question_tokens = set(re.findall(r"[a-zA-Z]+", question.lower()))
    ungrounded = []
    for word in question_tokens:
        if word in CHAT_CONCEPT_VOCABULARY and normalize_token(word) not in known_tokens:
            ungrounded.append(word)
    return sorted(set(ungrounded))


def get_sample_rows(dataset: Dataset, limit: int = 25, search: str | None = None) -> list[dict]:
    df = load_dataframe(dataset)
    if search:
        mask = df.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False)).any(axis=1)
        df = df[mask]
    df = df.head(limit)
    df = df.where(pd.notnull(df), None)
    rows = df.to_dict(orient="records")
    return [{"row_index": int(idx), "values": row} for idx, row in zip(df.index, rows)]


def build_dataset_profile(df: pd.DataFrame) -> dict:
    numeric_cols = []
    categorical_cols = []
    datetime_cols = []
    column_profiles = []

    for col in df.columns:
        series = df[col]
        semantic = detect_semantic_type(series, str(col))
        missing = int(series.isna().sum())
        unique = int(series.nunique(dropna=True))

        is_numeric = semantic == "numeric"
        is_categorical = semantic in ("categorical", "boolean")
        is_datetime = semantic == "datetime"
        is_id_like = is_id_like_column(str(col), unique, len(df), is_numeric)

        if is_numeric:
            numeric_cols.append(col)
        elif is_datetime:
            datetime_cols.append(col)
        else:
            categorical_cols.append(col)

        entry = {
            "name": str(col),
            "dtype": str(series.dtype),
            "semantic_type": semantic,
            "missing_count": missing,
            "missing_pct": round(missing / len(df) * 100, 2) if len(df) else 0,
            "unique_count": unique,
            "is_numeric": is_numeric,
            "is_categorical": is_categorical,
            "is_datetime": is_datetime,
            "is_id_like": is_id_like,
        }

        if is_numeric:
            desc = series.describe()
            entry["stats"] = {
                "min": float(desc.get("min", 0)) if pd.notna(desc.get("min")) else None,
                "max": float(desc.get("max", 0)) if pd.notna(desc.get("max")) else None,
                "mean": float(desc.get("mean", 0)) if pd.notna(desc.get("mean")) else None,
                "median": float(series.median()) if pd.notna(series.median()) else None,
                "std": float(desc.get("std", 0)) if pd.notna(desc.get("std")) else None,
            }
        elif is_categorical:
            top_values = series.value_counts(dropna=True).head(10)
            entry["top_values"] = [
                {"value": str(k), "count": int(v)} for k, v in top_values.items()
            ]

        column_profiles.append(entry)

    duplicate_rows = int(df.duplicated().sum())
    total_missing = int(df.isna().sum().sum())

    return {
        "row_count": int(df.shape[0]),
        "column_count": int(df.shape[1]),
        "numeric_columns": [str(c) for c in numeric_cols],
        "categorical_columns": [str(c) for c in categorical_cols],
        "datetime_columns": [str(c) for c in datetime_cols],
        "missing_values": total_missing,
        "duplicate_rows": duplicate_rows,
        "columns": column_profiles,
    }


def save_upload(
    db: Session,
    project_id,
    file_name: str,
    file_bytes: bytes,
) -> Dataset:
    file_type = validate_file_extension(file_name)
    validate_file_size(len(file_bytes), settings.max_upload_size_bytes)

    storage_path = generate_storage_path(settings.UPLOAD_DIR, file_name)
    os.makedirs(os.path.dirname(storage_path), exist_ok=True)
    with open(storage_path, "wb") as f:
        f.write(file_bytes)

    try:
        df = read_dataset_file(storage_path, file_type)
        validate_dataframe_structure(df)
        profile = build_dataset_profile(df)
    except DatasetValidationError:
        if os.path.exists(storage_path):
            os.remove(storage_path)
        raise

    dataset = Dataset(
        project_id=project_id,
        file_name=file_name,
        file_type=file_type,
        file_size=len(file_bytes),
        storage_path=storage_path,
        original_storage_path=storage_path,
        row_count=profile["row_count"],
        column_count=profile["column_count"],
        missing_values=profile["missing_values"],
        duplicate_rows=profile["duplicate_rows"],
        profile_json=profile,
    )
    db.add(dataset)
    db.flush()

    sync_dataset_columns(db, dataset.id, profile)

    db.commit()
    db.refresh(dataset)
    return dataset


def sync_dataset_columns(db: Session, dataset_id, profile: dict) -> None:
    """Replace a dataset's DatasetColumn rows with the columns in a freshly computed profile."""
    db.query(DatasetColumn).filter(DatasetColumn.dataset_id == dataset_id).delete()
    for col_profile in profile["columns"]:
        db.add(
            DatasetColumn(
                dataset_id=dataset_id,
                name=col_profile["name"],
                dtype=col_profile["dtype"],
                semantic_type=col_profile["semantic_type"],
                missing_count=col_profile["missing_count"],
                unique_count=col_profile["unique_count"],
                is_numeric=col_profile["is_numeric"],
                is_categorical=col_profile["is_categorical"],
                is_datetime=col_profile["is_datetime"],
                is_id_like=col_profile.get("is_id_like", False),
            )
        )


def load_dataframe(dataset: Dataset) -> pd.DataFrame:
    if not os.path.exists(dataset.storage_path):
        raise DatasetValidationError("The original dataset file could not be found on the server.")
    return read_dataset_file(dataset.storage_path, dataset.file_type)


def load_original_dataframe(dataset: Dataset) -> pd.DataFrame:
    """Always reads from the untouched, as-uploaded file so cleaning stays reproducible
    (each clean starts fresh from source instead of compounding on a previous clean)."""
    path = dataset.original_storage_path or dataset.storage_path
    if not os.path.exists(path):
        raise DatasetValidationError("The original dataset file could not be found on the server.")
    ext = os.path.splitext(path)[1].lower()
    return read_dataset_file(path, ext)
