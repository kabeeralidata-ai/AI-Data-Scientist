"""Reusable number/money formatting for reports and the API — kept separate from
feature_names.py (which humanizes column/feature NAMES, not values)."""

CURRENCY_NAME_HINTS = {
    "pkr": "PKR",
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "inr": "INR",
    "cad": "CAD",
    "aud": "AUD",
}


def detect_currency_from_column_name(col_name: str | None) -> str | None:
    """A column literally named with a currency code (e.g. 'total_pkr', 'price_usd')
    is real evidence of that dataset's currency — never guessed, never defaulted to a
    specific currency when no such evidence exists in the data itself."""
    if not col_name:
        return None
    tokens = str(col_name).lower().replace("-", "_").split("_")
    for token in tokens:
        if token in CURRENCY_NAME_HINTS:
            return CURRENCY_NAME_HINTS[token]
    return None


def format_money(value, currency: str | None = None) -> str:
    """Abbreviates a monetary amount to a readable scale (13,100,000 -> '13.1M') and
    prefixes it with a currency code ONLY when one was actually detected from the data
    (see detect_currency_from_column_name) — never asserts a currency the data didn't
    evidence. A raw, un-abbreviated number with no unit is a common source of confusion
    in a business report; this is the single place that formatting is decided."""
    if value is None:
        return "N/A"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    sign = "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        text = f"{abs_value / 1_000_000_000:,.1f}B"
    elif abs_value >= 1_000_000:
        text = f"{abs_value / 1_000_000:,.1f}M"
    elif abs_value >= 1_000:
        text = f"{abs_value / 1_000:,.1f}K"
    else:
        text = f"{abs_value:,.0f}"

    prefix = f"{currency} " if currency else ""
    return f"{prefix}{sign}{text}"
