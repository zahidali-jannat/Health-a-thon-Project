"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_env: str                   # "dev" shows one-time codes on screen and allows demo seeding
    db_path: Path
    storage_dir: Path              # root of the file store; files live under patients/{patient_id}/...
    clinician_session_hours: int = 12
    patient_session_hours: int = 24 * 7
    login_code_minutes: int = 10
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


def get_settings() -> Settings:
    return Settings(
        app_env=os.environ.get("APP_ENV", "dev"),
        db_path=Path(os.environ.get("UC2_DB_PATH", BACKEND_DIR / "uc2.db")),
        storage_dir=Path(os.environ.get("UC2_STORAGE_DIR", BACKEND_DIR / "storage")),
    )
