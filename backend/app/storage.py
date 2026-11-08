"""File storage. The database keeps metadata + storage_key; this module keeps the bytes.

Local disk, plus an optional copy in Supabase Storage (app/backup.py) for hosts with no lasting disk. Swapping to S3/GCS means reimplementing these three functions - nothing else
in the app touches file paths. Files are never served as static assets; downloads go through
authorized API routes only.
"""

from pathlib import Path

from . import backup
from .settings import get_settings

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic",
              "image/webp": ".webp", "application/pdf": ".pdf"}


def storage_key(patient_id: str, document_id: str, content_type: str) -> str:
    return f"patients/{patient_id}/documents/{document_id}{EXTENSIONS[content_type]}"


def _path(key: str) -> Path:
    root = get_settings().storage_dir.resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage key")
    return path


def _write(key: str, data: bytes) -> None:
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def save(key: str, data: bytes) -> None:
    _write(key, data)
    if backup.enabled():                    # a host whose disk is wiped on restart: keep a copy (app/backup.py)
        backup.put_file(key, data)


def read_local(key: str) -> bytes:
    return _path(key).read_bytes()


def read(key: str) -> bytes:
    try:
        return read_local(key)
    except FileNotFoundError:
        if not backup.enabled() or (data := backup.get_file(key)) is None:
            raise
        _write(key, data)                   # restored after a restart; kept on disk from now on
        return data


def delete(key: str) -> None:
    _path(key).unlink(missing_ok=True)
    if backup.enabled():
        backup.delete_file(key)
