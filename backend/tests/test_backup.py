"""Hosting with no lasting disk (Render free plan): the database and files are copied to Supabase Storage and
brought back after a restart, so accounts and passwords made on the site keep working. Uses a fake Supabase."""

import gzip
import json
import os
import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import backup, storage

BUCKET = "uc2-backup"


class FakeSupabase:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.buckets: set[str] = set()
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "test-secret"
        if self.down:
            return httpx.Response(503, text="unavailable")
        path = request.url.path.removeprefix("/storage/v1")
        if path == "/bucket" and request.method == "POST":
            name = json.loads(request.content)["id"]
            if name in self.buckets:
                return httpx.Response(400, json={"error": "Duplicate", "message": "The resource already exists"})
            self.buckets.add(name)
            return httpx.Response(200, json={"name": name})
        prefix = f"/object/{BUCKET}"
        if path == prefix and request.method == "DELETE":
            for key in json.loads(request.content)["prefixes"]:
                self.objects.pop(key, None)
            return httpx.Response(200, json=[])
        key = path.removeprefix(prefix + "/")
        if request.method == "POST":
            assert request.headers["x-upsert"] == "true"
            self.objects[key] = request.content
            return httpx.Response(200, json={"Key": f"{BUCKET}/{key}"})
        if request.method == "GET":
            if key not in self.objects:
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"})
            return httpx.Response(200, content=self.objects[key])
        return httpx.Response(405)


@pytest.fixture()
def supabase(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(backup, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(backup, "_lost_lease", False)
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    monkeypatch.setenv("UC2_BACKUP_URL", "https://example.supabase.co/")
    monkeypatch.setenv("UC2_BACKUP_KEY", "test-secret")
    return fake


def _hosted(monkeypatch, **env):
    """The app as Render runs it: production, public demo, codes on screen."""
    for k, v in {"APP_ENV": "prod", "UC2_SEED_DEMO": "1", "UC2_SHOW_CODES": "1", **env}.items():
        monkeypatch.setenv(k, v)
    from app.main import app
    return TestClient(app, base_url="https://testserver")     # production cookies are https-only


def _wipe_disk():
    """What a Render restart does: the database and every stored file are gone."""
    db = Path(os.environ["UC2_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(os.environ["UC2_STORAGE_DIR"], ignore_errors=True)


def _sign_up(c, phone="9123400000", password="Sunrise#2026x"):
    r = c.post("/api/auth/patient/signup", json={"full_name": "Asha Verma", "phone": phone, "password": password})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]                     # shown on screen: the demo has no SMS
    v = c.post("/api/auth/patient/signup/verify", json={"phone": phone, "code": code})
    assert v.status_code == 200, v.text


def _patient_login(c, identifier, password):
    return c.post("/api/auth/patient/login", json={"identifier": identifier, "password": password})


def test_a_new_account_and_a_changed_password_survive_a_restart(monkeypatch, supabase):
    with _hosted(monkeypatch) as c:
        _sign_up(c)
        assert _patient_login(c, "P-1001", "demo1234").status_code == 200
        r = c.post("/api/auth/patient/set-password", json={"current_password": "demo1234", "new_password": "Monsoon#2026x"})
        assert r.status_code == 200, r.text
    assert backup.DB_OBJECT in supabase.objects                 # shutdown uploaded the last changes

    _wipe_disk()
    with _hosted(monkeypatch) as c:
        assert _patient_login(c, "9123400000", "Sunrise#2026x").status_code == 200    # the new account is back
        assert _patient_login(c, "P-1001", "Monsoon#2026x").status_code == 200        # and the new password
        assert _patient_login(c, "P-1002", "demo1234").status_code == 200
        assert c.post("/api/auth/clinician/login", json={"identifier": "CLN-TNSQ01", "password": "demo1234"}).status_code == 200
    with sqlite3.connect(os.environ["UC2_DB_PATH"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 5   # 4 demo (not loaded twice) + Asha


def test_nothing_is_uploaded_when_nothing_changed(monkeypatch, supabase):
    with _hosted(monkeypatch):
        pass
    assert backup.DB_OBJECT not in supabase.objects      # start-up seeding alone is repeated on every start anyway
    assert backup.LEASE_OBJECT in supabase.objects


def test_the_server_refuses_to_start_empty_when_the_backup_cannot_be_read(monkeypatch, supabase):
    supabase.down = True
    with pytest.raises(backup.BackupError):
        backup.restore()
    assert not Path(os.environ["UC2_DB_PATH"]).exists()  # never an empty database that would overwrite the copy


def test_a_damaged_copy_is_refused(monkeypatch, supabase):
    supabase.objects[backup.DB_OBJECT] = gzip.compress(b"not a database at all, just some text" * 200)
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.restore()
    assert not Path(os.environ["UC2_DB_PATH"]).exists()


def test_a_local_database_is_never_replaced(monkeypatch, supabase, tmp_path):
    db = Path(os.environ["UC2_DB_PATH"])
    sqlite3.connect(db).execute("CREATE TABLE here (x)").connection.commit()
    supabase.objects[backup.DB_OBJECT] = b"whatever"
    assert backup.restore() is False
    assert sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchone()[0] == "here"


def test_uploaded_files_come_back_after_a_restart_and_deletes_reach_the_copy(supabase):
    key = "patients/p1/documents/d1.pdf"
    storage.save(key, b"%PDF-1.4 report")
    backup.flush()
    assert supabase.objects[backup.FILES_PREFIX + key] == b"%PDF-1.4 report"
    _wipe_disk()
    assert storage.read(key) == b"%PDF-1.4 report"       # downloaded once...
    supabase.objects.clear()
    assert storage.read(key) == b"%PDF-1.4 report"       # ...then kept on disk
    storage.delete(key)
    with pytest.raises(FileNotFoundError):
        storage.read(key)


def test_a_failed_file_upload_is_retried(supabase):
    key = "patients/p1/documents/d2.jpg"
    supabase.down = True
    storage.save(key, b"photo")
    backup.flush()
    assert key in backup._pending_files
    supabase.down = False
    backup._retry_files()
    backup.flush()
    assert supabase.objects[backup.FILES_PREFIX + key] == b"photo" and not backup._pending_files


def test_after_a_deploy_the_old_server_stops_uploading(supabase, tmp_path):
    db = Path(os.environ["UC2_DB_PATH"])
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    backup._take_lease()
    assert backup.upload_db(conn) is True
    supabase.objects[backup.LEASE_OBJECT] = json.dumps({"instance": "the-new-server"}).encode()
    supabase.objects.pop(backup.DB_OBJECT)
    assert backup.upload_db(conn) is False and backup.DB_OBJECT not in supabase.objects


def test_no_backup_settings_means_no_network(monkeypatch):
    monkeypatch.setattr(backup, "_transport", httpx.MockTransport(lambda r: pytest.fail("network used")))
    assert not backup.enabled()
    storage.save("patients/p1/documents/d3.pdf", b"x")
    assert storage.read("patients/p1/documents/d3.pdf") == b"x"


@pytest.mark.parametrize("show, expected", [("1", True), ("", False)])
def test_sign_up_codes_on_screen_only_when_switched_on(monkeypatch, show, expected):
    with _hosted(monkeypatch, UC2_SHOW_CODES=show) as c:
        r = c.post("/api/auth/patient/signup", json={"full_name": "Ravi Shah", "phone": "9123400001", "password": "Sunrise#2026x"})
        assert r.status_code == 200 and ("dev_code" in r.json()) is expected
        r = c.post("/api/auth/patient/request-code", json={"identifier": "P-1001"})
        assert r.status_code == 200 and ("dev_code" in r.json()) is expected
