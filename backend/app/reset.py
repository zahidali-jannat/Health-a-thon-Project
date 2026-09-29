"""Wipe the database and stored files and start completely empty. Run: python -m app.reset

Development only. There is no undo: every clinician, patient, document and log entry is deleted.
"""

import shutil
import sys

from . import db
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.is_dev:
        sys.exit("Refusing: APP_ENV is not 'dev'. This command never touches a real database.")
    if "--yes" not in sys.argv and input(f"Delete ALL data in {settings.db_path}? Type 'yes': ").strip() != "yes":
        sys.exit("Cancelled.")
    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    conn = db.reset()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("patients", "clinical_users", "patient_documents", "lab_reports", "clinical_events", "audit_log")}
    print(f"Fresh, empty database at {settings.db_path}: " + ", ".join(f"{t}={n}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
