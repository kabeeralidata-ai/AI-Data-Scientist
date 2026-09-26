import io
import random


def create_project(client, name="Retail Project", description=None):
    resp = client.post("/api/projects", json={"name": name, "description": description})
    assert resp.status_code == 201
    return resp.json()["id"]


def upload_csv(client, project_id, content, filename="data.csv"):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
    )


def _retail_csv(n=300):
    random.seed(3)
    cities = ["Karachi", "Lahore", "Islamabad", "Faisalabad", "Multan"]
    categories = ["Electronics", "Clothing", "Groceries", "Furniture", "Toys"]
    rows = [
        "order_id,order_date,store_city,product_category,sales_channel,customer_segment,"
        "unit_price,quantity,discount,customer_rating,sales_amount,profit,returns"
    ]
    for i in range(n):
        unit_price = round(random.uniform(5, 200), 2)
        qty = random.randint(1, 10)
        discount = round(random.uniform(0, 0.3), 2)
        sales_amount = round(unit_price * qty * (1 - discount), 2)
        profit = round(sales_amount * random.uniform(0.1, 0.3), 2)
        rows.append(
            f"ORD-{i},2024-{(i % 12) + 1:02d}-{(i % 27) + 1:02d},{random.choice(cities)},"
            f"{random.choice(categories)},{random.choice(['Online', 'In-store'])},"
            f"{random.choice(['Regular', 'Premium'])},{unit_price},{qty},{discount},"
            f"{random.randint(1, 5)},{sales_amount},{profit},{random.choice([0, 0, 0, 1])}"
        )
    return "\n".join(rows)


def test_target_detection_prefers_business_outcome_over_grouping_columns(auth_client):
    """Regression test for the bug where Auto Analyze suggested 'store_city' (a grouping
    column) as the target instead of 'sales_amount' — a low-cardinality grouping/
    descriptive column must never outrank a real numeric business outcome just because
    it happens to have few distinct values."""
    description = (
        "A synthetic dataset of retail orders from five Pakistani cities, covering order "
        "date, store city, product category, sales channel, customer segment, unit price, "
        "quantity, discount, customer rating, sales amount, profit, and returns."
    )
    project_id = create_project(auth_client, description=description)
    upload_resp = upload_csv(auth_client, project_id, _retail_csv())
    dataset_id = upload_resp.json()["id"]

    detail = auth_client.get(f"/api/datasets/{dataset_id}")
    assert detail.status_code == 200
    body = detail.json()

    assert body["suggested_target_column"] == "sales_amount"
    candidate_columns = [c["column"] for c in body["target_candidates"]]
    assert candidate_columns[0] == "sales_amount"
    assert "store_city" not in candidate_columns
    assert "product_category" not in candidate_columns
    for c in body["target_candidates"]:
        assert c["reason"]


def test_target_candidates_include_reasons_and_are_capped_at_three(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _retail_csv())
    dataset_id = upload_resp.json()["id"]

    detail = auth_client.get(f"/api/datasets/{dataset_id}")
    candidates = detail.json()["target_candidates"]
    assert 1 <= len(candidates) <= 3
    assert all(isinstance(c["score"], (int, float)) for c in candidates)
    assert all(c["problem_type_hint"] in ("classification", "regression") for c in candidates)


def _leakage_csv(n=100):
    rows = ["sales_amount,profit,quantity"]
    for i in range(1, n + 1):
        sales_amount = float(i) * 10
        profit = sales_amount * 0.2  # a fixed fraction of the target -> near-perfect correlation
        quantity = (i % 7) + 1
        rows.append(f"{sales_amount},{profit},{quantity}")
    return "\n".join(rows)


def test_training_excludes_leaked_features_and_reports_warning(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _leakage_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "sales_amount", "models": ["linear_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "profit" not in model["feature_columns_json"]
    assert "quantity" in model["feature_columns_json"]

    warnings = model["metrics_json"]["leakage_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["column"] == "profit"
    assert warnings[0]["correlation"] >= 0.95


def _unpredictable_csv(n=200):
    random.seed(99)
    rows = ["a,b,c,label"]
    for _ in range(n):
        a, b, c = random.uniform(0, 1), random.uniform(0, 1), random.uniform(0, 1)
        label = random.choice([0, 1])  # pure noise, unrelated to a/b/c
        rows.append(f"{a},{b},{c},{label}")
    return "\n".join(rows)


def test_weak_model_does_not_clearly_beat_baseline_on_unpredictable_target(auth_client):
    """A target that's genuinely unrelated to the features should honestly show the model
    failing to clearly beat a trivial baseline — not a hard-coded score cutoff, but a
    comparison against the model's own cross-validation variance."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _unpredictable_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "label", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    baseline = train_resp.json()[0]["metrics_json"]["baseline"]
    assert baseline["baseline_metric"] == "f1"
    assert baseline["clearly_beats_baseline"] is False


def _learnable_csv(n=200):
    random.seed(5)
    rows = ["spend,frequency,churn"]
    for _ in range(n):
        spend = random.uniform(10, 500)
        freq = random.randint(0, 20)
        churn = 1 if spend < 150 and freq < 5 else 0
        rows.append(f"{spend},{freq},{churn}")
    return "\n".join(rows)


def test_strong_model_clearly_beats_baseline_on_learnable_target(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _learnable_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["random_forest"]},
    )
    assert train_resp.status_code == 201
    baseline = train_resp.json()[0]["metrics_json"]["baseline"]
    assert baseline["clearly_beats_baseline"] is True


def _date_csv(n=60):
    random.seed(13)
    rows = ["customer_id,age,signup_date,amount"]
    for i in range(n):
        month = random.randint(1, 12)
        day = random.randint(1, 27)
        age = 20 + i % 40
        # amount depends on age (with noise), deliberately NOT a near-linear function of
        # the signup date, so this test isolates date-feature extraction from leakage
        # exclusion (a separate, already-covered behavior).
        amount = round(50 + age * 2.5 + random.uniform(-15, 15), 2)
        rows.append(f"CUST-{i},{age},2024-{month:02d}-{day:02d},{amount}")
    return "\n".join(rows)


def test_date_columns_are_extracted_into_derived_features_not_dropped(auth_client):
    """Instead of just dropping a raw date column, Auto/Smart training should extract
    year/month/day-of-week — genuinely useful seasonality signal a raw date string can't
    offer a model directly."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _date_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "amount", "models": ["linear_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]
    features = model["feature_columns_json"]

    assert "signup_date" not in features
    assert "signup_date_month" in features
    assert "signup_date_year" in features
    assert "signup_date_dayofweek" in features

    detail = auth_client.get(f"/api/models/{model['id']}")
    preprocessing = detail.json()["preprocessing_json"]
    derived = preprocessing["derived_date_features"]
    assert set(derived) == {"signup_date_year", "signup_date_month", "signup_date_dayofweek"}
    assert preprocessing["excluded_datetime_raw"] == ["signup_date"]


def _derived_flag_csv(n=300):
    """A binary target that's literally defined as one numeric column exceeding another —
    the exact shape of a real bug: 'late_delivery' == 'delivery_time_min' > 'promised_time_min'.
    Neither column alone is highly correlated with the target (each also depends on order
    size / distance), so single-column correlation-based leakage detection can't catch it —
    only checking the pair together can."""
    random.seed(21)
    rows = ["distance_km,delivery_time_min,promised_time_min,late_delivery"]
    for _ in range(n):
        distance = random.uniform(1, 15)
        promised = round(30 + distance * 3, 1)
        delivery = round(promised + random.uniform(-20, 20), 1)
        late = "Yes" if delivery > promised else "No"
        rows.append(f"{distance},{delivery},{promised},{late}")
    return "\n".join(rows)


def test_training_excludes_pairwise_derived_leakage_not_caught_by_single_column_correlation(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _derived_flag_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["decision_tree"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "delivery_time_min" not in model["feature_columns_json"]
    assert "promised_time_min" not in model["feature_columns_json"]
    assert "distance_km" in model["feature_columns_json"]

    warnings = model["metrics_json"]["leakage_warnings"]
    flagged = {w["column"] for w in warnings}
    assert flagged == {"delivery_time_min", "promised_time_min"}
    for w in warnings:
        assert w["correlation"] >= 0.95

    # Without the fix, a decision tree given both leaked columns trivially hits a
    # suspiciously perfect score; with them excluded, it's realistic.
    assert model["metrics_json"]["f1"] < 0.99


def test_single_column_leakage_check_alone_does_not_catch_the_pairwise_case(auth_client):
    """Sanity check that this really is a distinct detection path: the individual
    correlation of each column with the target is well below the leakage threshold, so
    only the pairwise check explains the exclusion above."""
    from app.services.ml_service import detect_leakage
    import pandas as pd
    import io as _io

    df = pd.read_csv(_io.StringIO(_derived_flag_csv()))
    single_column_warnings = detect_leakage(df, "late_delivery", ["distance_km", "delivery_time_min", "promised_time_min"])
    assert single_column_warnings == []


def _sla_tiered_flag_csv(n=300):
    """Shaped like the REAL karachi_food_delivery_dataset.csv (the benchmark that exposed
    this bug): 'promised_time_min' is a fixed SLA drawn from a small set of standard tiers
    (a handful of distinct values reused across many rows), while 'delivery_time_min' is
    the continuous, near-unique REALIZED outcome — a real, generalizable cardinality
    asymmetry between a planned/committed value and its outcome, unlike
    _derived_flag_csv()'s synthetic 'promised' (a continuous function of distance, with
    cardinality close to delivery_time_min's — deliberately a case where the two sides
    can't be confidently told apart, and both must still be excluded)."""
    random.seed(31)
    sla_tiers = [45, 60, 75]
    rows = ["distance_km,delivery_time_min,promised_time_min,late_delivery"]
    for _ in range(n):
        distance = random.uniform(1, 15)
        promised = sla_tiers[int(distance) % len(sla_tiers)]
        delivery = round(promised + random.uniform(-25, 25), 1)
        late = "Yes" if delivery > promised else "No"
        rows.append(f"{distance},{delivery},{promised},{late}")
    return "\n".join(rows)


def test_pairwise_leakage_excludes_only_the_outcome_side_when_cardinality_is_asymmetric(auth_client):
    """Regression test for the benchmark-found bug: when one side of a leaking pair is
    confidently identifiable (by cardinality alone, never by column name) as a
    planned/committed value known in advance, only the realized-outcome side must be
    excluded — the history side is legitimate, available-before-prediction information."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _sla_tiered_flag_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["decision_tree"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "delivery_time_min" not in model["feature_columns_json"]
    assert "promised_time_min" in model["feature_columns_json"]  # KEPT — the history side

    warnings = model["metrics_json"]["leakage_warnings"]
    flagged = {w["column"] for w in warnings}
    assert flagged == {"delivery_time_min"}  # NOT promised_time_min too
    assert "planned/committed" in warnings[0]["reason"] or "distinct values" in warnings[0]["reason"]


def _realistic_retail_csv(n=400):
    """Shaped like the real retail_sales_dataset.csv that exposed the original bug:
    profit is a NOISY ~10-30% margin of sales_amount (not an exact multiple), which
    correlates only ~0.85-0.90 with the target — high enough to be a real leak, but below
    the near-duplicate 0.95 threshold that a naive correlation-only check would require."""
    random.seed(55)
    rows = ["order_id,unit_price,quantity,discount_percent,sales_amount,profit"]
    for i in range(n):
        unit_price = round(random.uniform(5, 300), 2)
        qty = random.randint(1, 3)
        discount = round(random.uniform(0, 20), 1)
        sales_amount = round(unit_price * qty * (1 - discount / 100), 2)
        profit = round(sales_amount * random.uniform(0.10, 0.30), 2)
        rows.append(f"ORD-{i},{unit_price},{qty},{discount},{sales_amount},{profit}")
    return "\n".join(rows)


def test_realistic_noisy_profit_is_detected_and_excluded_from_retail_regression(auth_client):
    """The exact bug reported against the real retail dataset: a regression report showed
    R² ~= 0.98 with profit as a top feature, because profit's correlation with
    sales_amount (~0.85-0.90 with realistic noise) fell under the strict 0.95 near-
    duplicate threshold and was never flagged. profit must now be excluded as leakage,
    and unit_price (a legitimate pricing input, also highly correlated) must be retained."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _realistic_retail_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "sales_amount", "models": ["random_forest"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "profit" not in model["feature_columns_json"]
    assert "unit_price" in model["feature_columns_json"]
    assert "quantity" in model["feature_columns_json"]

    leaked = {w["column"] for w in model["metrics_json"]["leakage_warnings"]}
    assert "profit" in leaked

    high_corr = {w["column"] for w in model["metrics_json"]["high_correlation_warnings"]}
    assert "unit_price" in high_corr


def _post_outcome_csv(n=200):
    """tip_pkr and customer_rating each have a real, but roughly similar-strength,
    relationship with the target — enough to clear the "is this just noise" bar and be
    worth a review prompt, but with no single one of them a clear statistical outlier
    over the other, so neither should be auto-excluded."""
    random.seed(4)
    rows = ["order_value,distance_km,tip_pkr,customer_rating,late_delivery"]
    for _ in range(n):
        value = round(random.uniform(200, 2000), 2)
        distance = round(random.uniform(1, 15), 2)
        late = random.random() < 0.4
        tip = round(random.uniform(10, 40) if late else random.uniform(30, 70), 2)
        rating = round(random.uniform(1, 5) - (0.4 if late else 0) + random.uniform(-1.2, 1.2), 2)
        rows.append(f"{value},{distance},{tip},{rating},{'Yes' if late else 'No'}")
    return "\n".join(rows)


def test_post_outcome_named_features_are_excluded_by_default(auth_client):
    """A column named like a post-outcome field (tip, rating) that also shows a real
    relationship with the target is excluded from training BY DEFAULT — it could
    legitimately be known in advance (e.g. a rider's historical average rating), so it's
    not treated as confirmed leakage, but the safe default is to leave it out unless the
    user explicitly opts it back in."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _post_outcome_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "tip_pkr" not in model["feature_columns_json"]
    assert "customer_rating" not in model["feature_columns_json"]
    assert "order_value" in model["feature_columns_json"]

    excluded = {w["column"] for w in model["metrics_json"]["post_outcome_excluded"]}
    assert excluded == {"tip_pkr", "customer_rating"}
    assert model["metrics_json"]["post_outcome_included"] == []


def test_post_outcome_excluded_feature_can_be_re_included_and_retrained(auth_client):
    """The checkbox-driven override: a feature excluded by default can be explicitly
    brought back via include_post_outcome_features, and only that one — its
    similarly-suspicious sibling stays excluded unless also opted in."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _post_outcome_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={
            "dataset_id": dataset_id,
            "target_column": "late_delivery",
            "models": ["logistic_regression"],
            "include_post_outcome_features": ["tip_pkr"],
        },
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "tip_pkr" in model["feature_columns_json"]
    assert "customer_rating" not in model["feature_columns_json"]

    excluded = {w["column"] for w in model["metrics_json"]["post_outcome_excluded"]}
    assert excluded == {"customer_rating"}
    included = {w["column"] for w in model["metrics_json"]["post_outcome_included"]}
    assert included == {"tip_pkr"}


def test_post_outcome_name_match_with_no_real_relationship_is_not_flagged(auth_client):
    """Regression test: a column named like a post-outcome field (e.g. 'rider_rating')
    must NOT be flagged just because it matches the name heuristic, if it shows no real
    (above-noise-floor) relationship with THIS target in THIS dataset — a historical,
    known-in-advance rating is a legitimate feature, not a leak, and flagging it purely by
    name would be a false positive."""
    random.seed(17)
    n = 200
    rows = ["order_value,distance_km,rider_rating,late_delivery"]
    for _ in range(n):
        value = round(random.uniform(200, 2000), 2)
        distance = round(random.uniform(1, 15), 2)
        late = "Yes" if random.random() < 0.4 else "No"
        # rider_rating is a fixed historical value, independent of THIS delivery's lateness.
        rating = round(random.uniform(1, 5), 2)
        rows.append(f"{value},{distance},{rating},{late}")
    content = "\n".join(rows)

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert "rider_rating" in model["feature_columns_json"]
    flagged = {w["column"] for w in model["metrics_json"]["post_outcome_excluded"]}
    assert "rider_rating" not in flagged
    leaked = {w["column"] for w in model["metrics_json"]["leakage_warnings"]}
    assert "rider_rating" not in leaked


def test_dominant_post_outcome_feature_is_auto_excluded_not_just_flagged(auth_client):
    """The single-feature leakage test: among several post-outcome-named candidates, one
    that predicts the target far better than its similarly-named peers (a real, if
    partial, leak on its own) is excluded automatically rather than only flagged for
    review."""
    random.seed(11)
    n = 300
    rows = ["order_value,distance_km,tip_pkr,customer_rating,feedback_score,late_delivery"]
    for _ in range(n):
        value = round(random.uniform(200, 2000), 2)
        distance = round(random.uniform(1, 15), 2)
        late = random.random() < 0.4
        # tip_pkr: a clear statistical outlier among the post-outcome-named columns.
        tip = round(random.uniform(10, 45) if late else random.uniform(35, 90), 2)
        rating = round(random.uniform(1, 5) - (0.3 if late else 0) + random.uniform(-1.5, 1.5), 2)
        feedback = round(random.uniform(1, 10) - (0.6 if late else 0) + random.uniform(-2.5, 2.5), 2)
        rows.append(f"{value},{distance},{tip},{rating},{feedback},{'Yes' if late else 'No'}")
    content = "\n".join(rows)

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    # tip_pkr is a confirmed single-feature leak (unconditionally excluded); the other two
    # are only softly suspected by name+correlation, so they're excluded BY DEFAULT too,
    # but distinctly — as an overridable default, not a confirmed leak.
    assert "tip_pkr" not in model["feature_columns_json"]
    assert "customer_rating" not in model["feature_columns_json"]
    assert "feedback_score" not in model["feature_columns_json"]

    leaked = {w["column"] for w in model["metrics_json"]["leakage_warnings"]}
    assert "tip_pkr" in leaked
    excluded_by_default = {w["column"] for w in model["metrics_json"]["post_outcome_excluded"]}
    assert excluded_by_default == {"customer_rating", "feedback_score"}


def test_score_target_candidates_never_suggests_a_detected_formula_column():
    """Regression test: a formula column (e.g. total = quantity * price - discount) must
    never be suggested as a prediction target, even when its NAME would otherwise score
    well (e.g. 'total_amount' matches the 'amount' name hint) — formula_columns (a real,
    numerically-verified relationship) must override the name-hint score entirely."""
    from app.services.dataset_service import score_target_candidates

    profile = {
        "row_count": 200,
        "columns": [
            {"name": "quantity", "is_id_like": False, "is_datetime": False, "is_numeric": True, "unique_count": 15},
            {"name": "unit_price", "is_id_like": False, "is_datetime": False, "is_numeric": True, "unique_count": 40},
            # "total_amount" would normally score well: matches TARGET_HINT_TIER2 ("amount")
            # AND has many distinct values (a regression-candidate signal) — but it's a
            # real formula column here, so it must be excluded regardless.
            {"name": "total_amount", "is_id_like": False, "is_datetime": False, "is_numeric": True, "unique_count": 190},
        ],
    }
    formula_columns = [{"column": "total_amount", "formula": "total_amount = quantity * unit_price", "inputs": ["quantity", "unit_price"]}]

    without_formula_info = score_target_candidates(profile, description=None)
    assert any(c["column"] == "total_amount" for c in without_formula_info)  # confirms it WOULD have scored without the fix

    with_formula_info = score_target_candidates(profile, description=None, formula_columns=formula_columns)
    assert all(c["column"] != "total_amount" for c in with_formula_info)
