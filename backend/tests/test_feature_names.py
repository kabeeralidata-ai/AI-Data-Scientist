from app.utils.feature_names import humanize_column_name, humanize_feature_list, humanize_feature_name


def test_humanize_column_name_converts_snake_case():
    assert humanize_column_name("unit_price") == "Unit Price"
    assert humanize_column_name("store_city") == "Store City"


def test_humanize_column_name_converts_camel_case():
    assert humanize_column_name("salesAmount") == "Sales Amount"


def test_humanize_feature_name_plain_column_is_title_case():
    known = ["unit_price", "quantity"]
    assert humanize_feature_name("unit_price", known) == "Unit Price"


def test_humanize_feature_name_one_hot_encoded_becomes_column_colon_value():
    known = ["product_category", "payment_method", "region"]
    assert humanize_feature_name("product_category_Electronics", known) == "Product Category: Electronics"
    assert humanize_feature_name("payment_method_Credit_Card", known) == "Payment Method: Credit Card"
    assert humanize_feature_name("region_North", known) == "Region: North"


def test_humanize_feature_name_picks_the_longest_matching_column_prefix():
    """Regression guard: 'contract_type_Month-to-month' must resolve to the column
    'contract_type', not a shorter accidental prefix match."""
    known = ["contract_type", "contract"]
    result = humanize_feature_name("contract_type_Month-to-month", known)
    assert result.startswith("Contract Type:")


def test_humanize_feature_name_derived_date_parts():
    # When the base date column is NOT itself in known_columns (the real scenario for a
    # derived training feature — the raw date column is excluded from training and
    # replaced by these derived parts), it falls back to the '(Part)' suffix format.
    assert humanize_feature_name("order_date_month", []) == "Order Date (Month)"
    assert humanize_feature_name("signup_date_year", []) == "Signup Date (Year)"
    assert humanize_feature_name("signup_date_dayofweek", []) == "Signup Date (Day of Week)"
    # When the base column name IS known, the column-prefix match takes priority —
    # still a fully readable, correct label either way.
    assert humanize_feature_name("order_date_month", ["order_date"]) == "Order Date: Month"


def test_humanize_feature_name_falls_back_to_title_case_without_known_columns():
    assert humanize_feature_name("customer_rating", None) == "Customer Rating"


def test_humanize_feature_list_adds_label_and_preserves_original_fields():
    items = [{"name": "unit_price", "importance": 0.5}, {"name": "region_North", "importance": 0.2}]
    result = humanize_feature_list(items, ["unit_price", "region"])
    assert result[0] == {"name": "unit_price", "importance": 0.5, "label": "Unit Price"}
    assert result[1]["label"] == "Region: North"
    assert result[1]["importance"] == 0.2
