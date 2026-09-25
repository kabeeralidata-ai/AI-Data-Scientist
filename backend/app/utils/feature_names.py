"""Reusable feature-name humanization, so a technical/encoded column or one-hot feature
name (e.g. 'product_category_Electronics', 'order_date_month') never has to be fixed
individually wherever it's displayed — feature importance, model tables, charts,
correlations, predictions, excluded-feature explanations, AI prompts, report text."""

import re

_DERIVED_DATE_SUFFIXES = (
    ("_year", "Year"),
    ("_month", "Month"),
    ("_dayofweek", "Day of Week"),
)


def humanize_column_name(name: str) -> str:
    """'store_city' -> 'Store City', 'salesAmount' -> 'Sales Amount'."""
    s = re.sub(r"[-\s]+", "_", str(name))
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return " ".join(w.capitalize() for w in s.split("_") if w)


def _humanize_value(value: str) -> str:
    cleaned = str(value).replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return cleaned
    # Already looks like a proper label (mixed case, e.g. "Credit Card") — leave it be
    # rather than mangling deliberate capitalization.
    if any(c.isupper() for c in cleaned) and any(c.islower() for c in cleaned):
        return cleaned
    return " ".join(w.capitalize() for w in cleaned.split())


def humanize_feature_name(name: str, known_columns: list[str] | None = None) -> str:
    """Converts an encoded/technical feature name into a readable business label.

    - A plain original column name: simple Title Case ('unit_price' -> 'Unit Price').
    - A one-hot encoded feature ('product_category_Electronics') when the original
      column list is known: 'Product Category: Electronics'.
    - A derived date part ('order_date_month'): 'Order Date (Month)'.
    """
    name = str(name)

    if known_columns:
        matches = [c for c in known_columns if name == c or name.startswith(f"{c}_")]
        if matches:
            base = max(matches, key=len)
            if name == base:
                return humanize_column_name(base)
            value = name[len(base) + 1 :]
            return f"{humanize_column_name(base)}: {_humanize_value(value)}"

    for suffix, label in _DERIVED_DATE_SUFFIXES:
        if name.endswith(suffix):
            return f"{humanize_column_name(name[: -len(suffix)])} ({label})"

    return humanize_column_name(name)


def humanize_feature_list(
    items: list[dict], known_columns: list[str] | None = None, name_key: str = "name"
) -> list[dict]:
    """Returns a copy of a feature-importance-style list with a 'label' key added
    (the original 'name' is preserved for anything that still needs the raw column)."""
    result = []
    for item in items:
        entry = dict(item)
        entry["label"] = humanize_feature_name(str(item.get(name_key, "")), known_columns)
        result.append(entry)
    return result
