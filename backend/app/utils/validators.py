import os

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


class DatasetValidationError(Exception):
    """Raised when an uploaded dataset fails validation. Message is user-safe."""


def validate_file_extension(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DatasetValidationError(
            "Unsupported file type. Please upload a .csv, .xlsx, or .xls file."
        )
    return ext


def validate_file_size(size_bytes: int, max_size_bytes: int) -> None:
    if size_bytes <= 0:
        raise DatasetValidationError("The uploaded file appears to be empty.")
    if size_bytes > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        raise DatasetValidationError(
            f"The uploaded file is too large. Maximum allowed size is {max_mb} MB."
        )


def validate_dataframe_structure(df) -> None:
    if df is None or df.shape[1] == 0:
        raise DatasetValidationError(
            "We could not process this file because the uploaded spreadsheet "
            "does not contain a valid header row."
        )
    if df.shape[0] == 0:
        raise DatasetValidationError("The uploaded dataset does not contain any data rows.")

    columns = list(df.columns)
    normalized = [str(c).strip() for c in columns]
    if any(c == "" or c.lower().startswith("unnamed") for c in normalized):
        raise DatasetValidationError(
            "We could not process this file because the uploaded spreadsheet "
            "does not contain a valid header row."
        )
    if len(set(normalized)) != len(normalized):
        raise DatasetValidationError(
            "The dataset contains duplicate column names. Please rename columns and try again."
        )
