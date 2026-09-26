import os

import joblib
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.ml_model import MLModel
from app.models.prediction import Prediction
from app.services.ml_service import derive_date_features_for_prediction
from app.utils.validators import DatasetValidationError

MAX_BATCH_ROWS = 5000


def _to_native(value):
    if pd.isna(value):
        return None
    if isinstance(value, (np.generic,)):
        return value.item()
    return value


def _load_bundle(model_record: MLModel) -> dict:
    if not model_record.model_path or not os.path.exists(model_record.model_path):
        raise DatasetValidationError("The trained model file could not be found on the server.")
    return joblib.load(model_record.model_path)


def _compute_contributions(bundle: dict, model_record: MLModel, input_df: pd.DataFrame, predicted_class_index: int | None) -> dict:
    """Real, exact per-prediction contributions for linear models (coefficient x scaled
    input value). Tree ensembles have no cheap exact per-instance equivalent without SHAP,
    so we fall back to the model's already-computed global feature importance and label it
    honestly as such rather than presenting it as instance-specific."""
    pipeline = bundle["pipeline"]
    model = pipeline.named_steps["model"]
    preprocessor = pipeline.named_steps["preprocessor"]
    numeric_features = bundle.get("numeric_features", [])
    categorical_features = bundle.get("categorical_features", [])

    if not hasattr(model, "coef_"):
        return {
            "type": "global_importance",
            "note": "This model type does not support exact per-prediction contributions; "
            "showing the features that were most influential across the whole model instead.",
            "items": (model_record.feature_importance_json or [])[:8],
        }

    try:
        transformed = preprocessor.transform(input_df)
        if hasattr(transformed, "toarray"):
            transformed = transformed.toarray()
        transformed = np.asarray(transformed)[0]

        feature_names = list(numeric_features)
        if categorical_features:
            cat_encoder = preprocessor.named_transformers_["cat"].named_steps["encoder"]
            feature_names += list(cat_encoder.get_feature_names_out(categorical_features))

        coef = model.coef_
        if coef.ndim > 1 and coef.shape[0] > 1 and predicted_class_index is not None:
            row = coef[predicted_class_index % coef.shape[0]]
        elif coef.ndim > 1:
            row = coef[0]
        else:
            row = coef

        if len(row) != len(transformed) or len(feature_names) != len(transformed):
            raise ValueError("Dimension mismatch computing contributions.")

        raw_contribs = row * transformed

        # Fold one-hot dummy contributions back onto their source categorical column.
        merged: dict[str, float] = {}
        for name, value in zip(feature_names, raw_contribs):
            base = name
            for cat in categorical_features:
                if name.startswith(f"{cat}_"):
                    base = cat
                    break
            merged[base] = merged.get(base, 0.0) + float(value)

        items = sorted(
            ({"name": k, "contribution": round(v, 4)} for k, v in merged.items()),
            key=lambda x: abs(x["contribution"]),
            reverse=True,
        )
        return {
            "type": "instance_contribution",
            "note": "Exact contribution of each feature to this specific prediction "
            "(coefficient x scaled feature value).",
            "items": items[:10],
        }
    except Exception:
        return {
            "type": "global_importance",
            "note": "Per-prediction contributions could not be computed for this input; "
            "showing globally influential features instead.",
            "items": (model_record.feature_importance_json or [])[:8],
        }


def predict(db: Session, model_record: MLModel, features: dict) -> Prediction:
    bundle = _load_bundle(model_record)
    pipeline = bundle["pipeline"]
    label_map = bundle.get("label_map")
    all_features = model_record.feature_columns_json or []

    input_df = pd.DataFrame([features])
    input_df = derive_date_features_for_prediction(input_df, model_record.preprocessing_json or {})

    missing = [f for f in all_features if f not in input_df.columns]
    if missing:
        raise DatasetValidationError(
            f"Missing required feature values: {', '.join(missing)}."
        )

    row = {f: _to_native(input_df.iloc[0][f]) for f in all_features}
    input_df = pd.DataFrame([row])

    raw_pred = pipeline.predict(input_df)[0]

    output: dict = {}
    predicted_class_index: int | None = None
    if model_record.problem_type == "classification":
        predicted_class_index = int(raw_pred)
        reverse_map = {v: k for k, v in (label_map or {}).items()}
        predicted_label = reverse_map.get(int(raw_pred), str(raw_pred))
        output["predicted_class"] = predicted_label
        if hasattr(pipeline, "predict_proba"):
            proba = pipeline.predict_proba(input_df)[0]
            output["probabilities"] = {
                reverse_map.get(i, str(i)): round(float(p), 4) for i, p in enumerate(proba)
            }
            output["confidence"] = round(float(max(proba)), 4)
    else:
        output["predicted_value"] = round(float(raw_pred), 4)

    output["contributions"] = _compute_contributions(bundle, model_record, input_df, predicted_class_index)

    prediction = Prediction(model_id=model_record.id, input_json=row, output_json=output)
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction


def predict_batch(model_record: MLModel, rows_df: pd.DataFrame) -> list[dict]:
    """Synchronous batch inference for a CSV of rows. Capped at MAX_BATCH_ROWS to keep this
    a request/response operation; larger files should be chunked by the user."""
    if len(rows_df) > MAX_BATCH_ROWS:
        raise DatasetValidationError(
            f"Batch prediction supports at most {MAX_BATCH_ROWS} rows per file; this file has {len(rows_df)}."
        )

    bundle = _load_bundle(model_record)
    pipeline = bundle["pipeline"]
    label_map = bundle.get("label_map")
    all_features = model_record.feature_columns_json or []

    rows_df = derive_date_features_for_prediction(rows_df, model_record.preprocessing_json or {})

    missing_cols = [f for f in all_features if f not in rows_df.columns]
    if missing_cols:
        raise DatasetValidationError(
            f"The uploaded file is missing required column(s): {', '.join(missing_cols)}."
        )

    input_df = rows_df[all_features].copy()
    predictions = pipeline.predict(input_df)
    proba_matrix = None
    if model_record.problem_type == "classification" and hasattr(pipeline, "predict_proba"):
        proba_matrix = pipeline.predict_proba(input_df)

    reverse_map = {v: k for k, v in (label_map or {}).items()} if label_map else {}

    results = []
    for i in range(len(input_df)):
        row_result = {"row_index": int(i), **{f: _to_native(input_df.iloc[i][f]) for f in all_features}}
        if model_record.problem_type == "classification":
            pred_idx = int(predictions[i])
            row_result["prediction"] = reverse_map.get(pred_idx, str(pred_idx))
            if proba_matrix is not None:
                row_result["confidence"] = round(float(max(proba_matrix[i])), 4)
        else:
            row_result["prediction"] = round(float(predictions[i]), 4)
        results.append(row_result)

    return results
