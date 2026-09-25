import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)


def classification_metrics(y_true, y_pred, y_proba=None) -> dict:
    metrics = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
    }
    if y_proba is not None:
        try:
            n_classes = len(set(y_true))
            if n_classes == 2:
                metrics["roc_auc"] = round(float(roc_auc_score(y_true, y_proba[:, 1])), 4)
            else:
                metrics["roc_auc"] = round(
                    float(roc_auc_score(y_true, y_proba, multi_class="ovr", average="weighted")), 4
                )
        except Exception:
            metrics["roc_auc"] = None
    return metrics


def regression_metrics(y_true, y_pred) -> dict:
    mse = mean_squared_error(y_true, y_pred)
    metrics = {
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
        "mse": round(float(mse), 4),
        "rmse": round(float(np.sqrt(mse)), 4),
        "r2": round(float(r2_score(y_true, y_pred)), 4),
    }
    try:
        metrics["mape"] = round(float(mean_absolute_percentage_error(y_true, y_pred)), 4)
    except Exception:
        metrics["mape"] = None
    return metrics
