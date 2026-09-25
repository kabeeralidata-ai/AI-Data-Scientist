import os
import uuid

import pandas as pd

from app.utils.validators import DatasetValidationError


def generate_storage_path(base_dir: str, original_filename: str) -> str:
    ext = os.path.splitext(original_filename)[1].lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    return os.path.join(base_dir, unique_name)


def read_dataset_file(path: str, file_type: str) -> pd.DataFrame:
    try:
        if file_type == ".csv":
            return pd.read_csv(path)
        elif file_type in (".xlsx", ".xls"):
            return pd.read_excel(path)
        else:
            raise DatasetValidationError("Unsupported file type.")
    except DatasetValidationError:
        raise
    except UnicodeDecodeError:
        raise DatasetValidationError(
            "We could not read this file due to an unsupported text encoding. "
            "Please save the file as UTF-8 and try again."
        )
    except Exception:
        raise DatasetValidationError(
            "We could not process this file. Please verify it is a valid CSV or Excel file."
        )
