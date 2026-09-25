"""Data Understanding step — runs BEFORE cleaning, so cleaning/analysis decisions can be
informed by what each column actually represents rather than generic numeric/categorical
heuristics alone.

1. classify_columns(): assigns each column a business ROLE (identifier, customer_id,
   timestamp, money, quantity, category, other) and whether it can legitimately be
   negative — rule-based first (deterministic, always available even if Gemini is down),
   with an OPTIONAL Gemini cross-check that only ever raises confidence on a low-confidence
   rule-based guess, never overrides a high-confidence one. Gemini is sent column names and
   summary statistics ONLY — never raw rows.
2. detect_dataset_type(): transaction_log / customer_level / time_series / general, from
   the classified roles plus a real check of rows-per-identifier (not just column presence).
3. detect_formula_columns(): finds a numeric column that's an (almost) exact arithmetic
   function of other numeric columns — e.g. total = quantity * unit_price - discount — so
   callers can exclude it from ever being chosen as a prediction target or "explained" by
   its own inputs.
"""

import logging
import re

import numpy as np
import pandas as pd

from app.models.dataset import Dataset
from app.services import ai_service
from app.services.ai_service import AIUnavailableError
from app.services.dataset_service import load_original_dataframe, tokenize_column_name, tokens_match_any

logger = logging.getLogger("data_understanding")

ROLE_IDENTIFIER = "identifier"
ROLE_CUSTOMER_ID = "customer_id"
ROLE_TIMESTAMP = "timestamp"
ROLE_MONEY = "money"
ROLE_QUANTITY = "quantity"
ROLE_CATEGORY = "category"
ROLE_OTHER = "other"

CUSTOMER_ID_HINTS = ("customer", "client", "member", "subscriber", "patron", "shopper")
GENERIC_ID_HINTS = ("id", "uuid", "guid", "code")
TIMESTAMP_HINTS = ("date", "time", "timestamp", "datetime", "created", "updated", "at")
MONEY_HINTS = (
    "price", "amount", "total", "revenue", "cost", "fee", "discount", "charge",
    "pkr", "usd", "salary", "spend", "value", "payment", "sales", "profit",
)
QUANTITY_HINTS = ("quantity", "qty", "count", "units", "items", "orders")
CATEGORY_HINTS = ("category", "type", "branch", "channel", "segment", "region", "method", "status", "gender")

# Roles where a negative value is a legitimate real-world signal (a refund/return reduces
# quantity and revenue) rather than a data-entry error — used by cleaning_service to decide
# whether rare negatives should be preserved or treated as likely mistakes.
NEGATIVE_CAPABLE_ROLES = (ROLE_MONEY, ROLE_QUANTITY)

# Same currency/unit-tolerant shape as cleaning_service's unit-stripping pattern — used
# here only to decide whether a column is numeric-LIKE for role classification purposes,
# so a money/quantity column doesn't get mis-classified as "category" just because a
# handful of rows still carry a "PKR 820" style prefix at this point (before cleaning
# has run — Data Understanding deliberately runs before cleaning).
_NUMERIC_LIKE_PATTERN = re.compile(
    r"^\s*(?:rs\.?|pkr|usd|\$|€|£)?\s*[+-]?[\d,]+\.?\d*\s*(?:kg|g|lbs?|%|pkr|rs\.?|usd|\$)?\s*$", re.IGNORECASE
)


def _numeric_like_fraction(series: pd.Series) -> float:
    non_null = series.dropna().astype(str)
    if len(non_null) == 0:
        return 0.0
    return float(non_null.map(lambda v: bool(_NUMERIC_LIKE_PATTERN.match(v))).mean())


def _parses_as_dates(series: pd.Series, sample: int = 200) -> float:
    non_null = series.dropna()
    if len(non_null) == 0:
        return 0.0
    sample_series = non_null.head(sample)
    parsed = pd.to_datetime(sample_series, errors="coerce")
    return float(parsed.notna().mean())


def _classify_one_column(name: str, series: pd.Series, semantic_type: str, unique_count: int, row_count: int) -> dict:
    tokens = tokenize_column_name(name)
    is_numeric = semantic_type == "numeric"

    # Requires BOTH a customer/client-type word AND an id/code word together (e.g.
    # "customer_id", "client_code") — a lone customer-hint match (e.g. "customer_rating",
    # "customer_feedback") is NOT an identifier and must not be treated as one, or
    # anything with a "customer_X" rating/feedback/satisfaction column would falsely
    # trigger transaction-log detection on datasets that have no real customer ID at all.
    if tokens_match_any(tokens, CUSTOMER_ID_HINTS) and tokens_match_any(tokens, GENERIC_ID_HINTS):
        return {
            "role": ROLE_CUSTOMER_ID,
            "can_be_negative": False,
            "confidence": "high",
            "evidence": "column name references a customer/account identifier",
        }

    # A NUMERIC column can never genuinely be a timestamp, no matter its name — a numeric
    # "prep_time_min"/"delivery_time_min" is a DURATION, not a date, so the name-hint
    # shortcut below is restricted to non-numeric columns (an actual date column is never
    # stored as a plain number here).
    date_parse_rate = _parses_as_dates(series) if not is_numeric else 0.0
    if semantic_type == "datetime" or (not is_numeric and tokens_match_any(tokens, TIMESTAMP_HINTS)) or date_parse_rate > 0.5:
        confidence = "high" if (semantic_type == "datetime" or date_parse_rate > 0.9) else "medium"
        return {
            "role": ROLE_TIMESTAMP,
            "can_be_negative": False,
            "confidence": confidence,
            "evidence": f"semantic type is datetime or {round(date_parse_rate * 100)}% of sampled values parse as dates",
        }

    if tokens_match_any(tokens, GENERIC_ID_HINTS) or (
        not is_numeric and row_count >= 10 and unique_count >= row_count * 0.95
    ):
        return {
            "role": ROLE_IDENTIFIER,
            "can_be_negative": False,
            "confidence": "high",
            "evidence": "near-unique, identifier-shaped column",
        }

    # Numeric-LIKE, not strictly numeric dtype — Data Understanding runs BEFORE cleaning,
    # so a money/quantity column contaminated with a few "PKR 820"-style values (still
    # numeric in substance) must not fall through to "category" just because pandas
    # currently reads the whole column as text.
    is_numeric_like = 1.0 if is_numeric else _numeric_like_fraction(series)
    if is_numeric_like >= 0.9 and tokens_match_any(tokens, MONEY_HINTS):
        return {
            "role": ROLE_MONEY,
            "can_be_negative": True,
            "confidence": "high",
            "evidence": "column name references a monetary amount",
        }

    if is_numeric_like >= 0.9 and tokens_match_any(tokens, QUANTITY_HINTS):
        return {
            "role": ROLE_QUANTITY,
            "can_be_negative": True,
            "confidence": "high",
            "evidence": "column name references a count/quantity",
        }

    if semantic_type in ("categorical", "boolean") or tokens_match_any(tokens, CATEGORY_HINTS):
        return {
            "role": ROLE_CATEGORY,
            "can_be_negative": False,
            "confidence": "medium" if semantic_type in ("categorical", "boolean") else "low",
            "evidence": "low-cardinality categorical column" if semantic_type in ("categorical", "boolean")
            else "column name suggests a category/grouping field",
        }

    return {
        "role": ROLE_OTHER,
        # NOT "is_numeric" — can_be_negative is a positive claim that needs actual
        # evidence (a money/quantity role), not a default assumption for an unclassified
        # numeric column. A duration/count-like column with no name hint (e.g.
        # "delivery_minutes") must still be eligible for the rare-negative-value quality
        # check in cleaning_service, exactly like before Data Understanding existed.
        "can_be_negative": False,
        "confidence": "low",
        "evidence": "no strong name or type signal",
    }


def _column_stats_for_gemini(name: str, series: pd.Series, semantic_type: str) -> dict:
    """Names and SUMMARY statistics only — never a single raw row/value that could
    identify a real customer or transaction. Matches the same sanitization discipline
    already used for context_service's Gemini prompts elsewhere in this app."""
    stats: dict = {"name": name, "dtype": semantic_type}
    non_null = series.dropna()
    if semantic_type == "numeric" and len(non_null) > 0:
        stats["min"] = round(float(non_null.min()), 2)
        stats["max"] = round(float(non_null.max()), 2)
        stats["mean"] = round(float(non_null.mean()), 2)
        stats["pct_negative"] = round(float((non_null < 0).mean()) * 100, 2)
    elif len(non_null) > 0:
        stats["unique_count"] = int(non_null.nunique())
        stats["sample_distinct_values"] = [str(v) for v in non_null.astype(str).unique()[:5]]
    return stats


def _gemini_cross_check_roles(rule_based: dict[str, dict], df: pd.DataFrame, semantic_types: dict[str, str]) -> dict[str, dict]:
    """Best-effort secondary signal — only used to raise confidence on columns the
    rule-based pass was NOT confident about (confidence == 'low'). A Gemini suggestion is
    only accepted if it names a valid role AND the low-confidence rule-based guess didn't
    already strongly disagree via computed evidence (e.g. Gemini can't turn a column that
    is 95% unique text into a 'money' column). Never sent raw data — see
    _column_stats_for_gemini. Fully skipped (no crash, no delay) if Gemini isn't
    configured/reachable, since this must never block the (rule-based, always-correct)
    Data Understanding step."""
    low_confidence_cols = {name: info for name, info in rule_based.items() if info["confidence"] == "low"}
    if not low_confidence_cols:
        return rule_based

    column_stats = [_column_stats_for_gemini(name, df[name], semantic_types.get(name, "other")) for name in low_confidence_cols]
    valid_roles = {ROLE_IDENTIFIER, ROLE_CUSTOMER_ID, ROLE_TIMESTAMP, ROLE_MONEY, ROLE_QUANTITY, ROLE_CATEGORY, ROLE_OTHER}

    prompt_context = {
        "task": "Classify each column's business role from its name and summary statistics only.",
        "valid_roles": sorted(valid_roles),
        "columns": column_stats,
    }
    try:
        raw, _provider = ai_service.generate_insight(
            {
                **prompt_context,
                "instruction": (
                    "Reply with ONLY a JSON object mapping each column name to one role from "
                    "valid_roles. No other text."
                ),
            }
        )
    except AIUnavailableError as exc:
        logger.info("Gemini column-role cross-check skipped (%s) — using rule-based roles only.", exc.reason)
        return rule_based
    except Exception:
        logger.info("Gemini column-role cross-check failed unexpectedly — using rule-based roles only.")
        return rule_based

    import json
    import re

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return rule_based
    try:
        suggested = json.loads(match.group(0))
    except Exception:
        return rule_based

    merged = dict(rule_based)
    for name, suggested_role in suggested.items():
        if name not in low_confidence_cols or suggested_role not in valid_roles:
            continue
        merged[name] = {
            **rule_based[name],
            "role": suggested_role,
            "confidence": "medium",
            "evidence": rule_based[name]["evidence"] + " (raised via Gemini name/stats cross-check)",
            "can_be_negative": suggested_role in NEGATIVE_CAPABLE_ROLES,
        }
    return merged


def classify_columns(df: pd.DataFrame, profile: dict, use_gemini: bool = True) -> dict[str, dict]:
    """Returns {column_name: {role, can_be_negative, confidence, evidence}}."""
    columns_meta = {c["name"]: c for c in profile.get("columns", [])}
    row_count = profile.get("row_count", len(df))

    roles: dict[str, dict] = {}
    semantic_types: dict[str, str] = {}
    for col in df.columns:
        meta = columns_meta.get(str(col), {})
        semantic_type = meta.get("semantic_type") or (
            "numeric" if pd.api.types.is_numeric_dtype(df[col]) else "text"
        )
        semantic_types[str(col)] = semantic_type
        unique_count = meta.get("unique_count", int(df[col].nunique(dropna=True)))
        roles[str(col)] = _classify_one_column(str(col), df[col], semantic_type, unique_count, row_count)

    if use_gemini:
        roles = _gemini_cross_check_roles(roles, df, semantic_types)

    return roles


def detect_dataset_type(df: pd.DataFrame, column_roles: dict[str, dict]) -> dict:
    """Returns {"type": ..., "reason": ...}. Verifies "transaction log" with an actual
    rows-per-identifier check (not just column presence), so a customer-level table that
    happens to also have a money column and a signup date isn't misclassified."""
    customer_id_cols = [n for n, r in column_roles.items() if r["role"] == ROLE_CUSTOMER_ID]
    identifier_cols = [n for n, r in column_roles.items() if r["role"] == ROLE_IDENTIFIER]
    has_money = any(r["role"] == ROLE_MONEY for r in column_roles.values())
    has_timestamp = any(r["role"] == ROLE_TIMESTAMP for r in column_roles.values())

    if customer_id_cols and has_money and has_timestamp:
        id_col = customer_id_cols[0]
        non_null_ids = df[id_col].dropna()
        rows_per_customer = (len(non_null_ids) / non_null_ids.nunique()) if non_null_ids.nunique() else 0
        if rows_per_customer >= 1.5:
            return {
                "type": "transaction_log",
                "reason": (
                    f"has a customer identifier ('{id_col}'), a monetary column, and a timestamp, "
                    f"averaging {round(rows_per_customer, 1)} rows per identified customer"
                ),
                "customer_id_column": id_col,
            }

    if has_timestamp and has_money and not customer_id_cols:
        return {
            "type": "time_series",
            "reason": "has a timestamp and a monetary/measured column but no per-record customer identifier",
        }

    if customer_id_cols and not has_timestamp:
        id_col = customer_id_cols[0]
        non_null_ids = df[id_col].dropna()
        if non_null_ids.nunique() and (len(non_null_ids) / non_null_ids.nunique()) < 1.5:
            return {
                "type": "customer_level",
                "reason": f"has a customer identifier ('{id_col}') with (nearly) one row per customer and no timestamp",
            }

    if identifier_cols and not customer_id_cols and has_timestamp:
        return {
            "type": "time_series",
            "reason": "has a timestamp and a row identifier but no customer-level identifier",
        }

    return {
        "type": "general",
        "reason": "no combination of customer identifier, timestamp, and monetary column strongly suggests a "
        "transaction log, time series, or customer-level table",
    }


def _series_matches(y: pd.Series, computed: pd.Series, min_match_rate: float = 0.97) -> bool:
    aligned = pd.concat([y, computed], axis=1).dropna()
    if len(aligned) < 10:
        return False
    a, b = aligned.iloc[:, 0], aligned.iloc[:, 1]
    tolerance = a.abs().median() * 0.02 + 0.5
    match_rate = ((a - b).abs() <= tolerance).mean()
    return bool(match_rate >= min_match_rate)


def detect_formula_columns(df: pd.DataFrame, column_roles: dict[str, dict]) -> list[dict]:
    """Restricted to MONEY-role target candidates built from MONEY/QUANTITY-role inputs —
    covers the common 'total = quantity * price - discount' transaction shape precisely,
    without an unbounded brute-force search across every numeric column pair/triple."""
    money_cols = [n for n, r in column_roles.items() if r["role"] == ROLE_MONEY and n in df.columns]
    quantity_cols = [n for n, r in column_roles.items() if r["role"] == ROLE_QUANTITY and n in df.columns]
    input_candidates = money_cols + quantity_cols
    if len(money_cols) < 1 or len(input_candidates) < 2:
        return []

    numeric_df = df[list(set(input_candidates))].apply(pd.to_numeric, errors="coerce")
    results: list[dict] = []

    for target in money_cols:
        y = numeric_df[target]
        if y.notna().sum() < 10:
            continue
        others = [c for c in input_candidates if c != target]

        found_formula = None
        # Pairwise sum/difference.
        for i, a in enumerate(others):
            for b in others[i + 1 :]:
                if _series_matches(y, numeric_df[a] + numeric_df[b]):
                    found_formula = {"formula": f"{target} = {a} + {b}", "inputs": [a, b]}
                elif _series_matches(y, numeric_df[a] - numeric_df[b]):
                    found_formula = {"formula": f"{target} = {a} - {b}", "inputs": [a, b]}
                elif _series_matches(y, numeric_df[b] - numeric_df[a]):
                    found_formula = {"formula": f"{target} = {b} - {a}", "inputs": [a, b]}
                if found_formula:
                    break
            if found_formula:
                break

        # Product of two inputs, optionally adjusted by a third (covers qty*price-discount).
        if not found_formula:
            for i, a in enumerate(others):
                for b in others[i + 1 :]:
                    product = numeric_df[a] * numeric_df[b]
                    if _series_matches(y, product):
                        found_formula = {"formula": f"{target} = {a} * {b}", "inputs": [a, b]}
                        break
                    for c in others:
                        if c in (a, b):
                            continue
                        if _series_matches(y, product - numeric_df[c]):
                            found_formula = {"formula": f"{target} = {a} * {b} - {c}", "inputs": [a, b, c]}
                            break
                        if _series_matches(y, product + numeric_df[c]):
                            found_formula = {"formula": f"{target} = {a} * {b} + {c}", "inputs": [a, b, c]}
                            break
                    if found_formula:
                        break
                if found_formula:
                    break

        if found_formula:
            results.append(
                {
                    "column": target,
                    "formula": found_formula["formula"],
                    "inputs": found_formula["inputs"],
                    "reason": (
                        f"'{target}' is (almost) exactly reproduced by {found_formula['formula'].split('= ')[1]} — "
                        "a formula/derived column, not an independent outcome."
                    ),
                }
            )

    return results


def understand_dataset(dataset: Dataset, use_gemini: bool = False) -> dict:
    """Convenience entry point used by both the cleaning/quality endpoints and the
    transaction-log analysis planner — runs the full Data Understanding step (column
    roles, dataset type, formula columns) against a dataset's ORIGINAL (pre-cleaning)
    data, exactly as the spec requires this to run BEFORE cleaning decisions are made.
    `use_gemini` defaults to False for synchronous request-path callers (quality/clean
    endpoints) to avoid adding real-API latency/failure risk to an interactive call; the
    planner (a deliberate, one-off analysis step) opts in."""
    df = load_original_dataframe(dataset)
    profile = dataset.profile_json or {}
    column_roles = classify_columns(df, profile, use_gemini=use_gemini)
    dataset_type = detect_dataset_type(df, column_roles)
    formula_columns = detect_formula_columns(df, column_roles)
    return {"column_roles": column_roles, "dataset_type": dataset_type, "formula_columns": formula_columns}
