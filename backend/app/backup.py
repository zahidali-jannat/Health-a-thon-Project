"""Keep a copy of the database and uploaded files in a Supabase Storage bucket.

For hosts whose disk is wiped on every restart (Render's free plan): accounts, passwords and reports survive.
Off unless UC2_BACKUP_URL and UC2_BACKUP_KEY are set, so a local run never talks to the network.

- Start-up: when the database file does not exist yet, download the last copy. If the bucket cannot be read,
  start-up fails instead of continuing with an empty database (which would then overwrite the good copy).
- Running: a background thread checks every few seconds whether anything was committed and, if so, uploads a
  consistent snapshot (SQLite backup API, gzipped). Shutdown uploads one last time.
- Files: storage.save uploads each file in the background as it is saved; storage.read downloads a file that is not on disk yet.
- Deploys: the new server writes a lease at start-up. A server whose lease was taken over stops uploading, so
  the old server shutting down after a deploy can never overwrite the new one's data.
"""

import gzip
import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path

import httpx

from .settings import get_settings

log = logging.getLogger("uc2.backup")

DB_OBJECT = "db/uc2.db.gz"
LEASE_OBJECT = "db/lease.json"
FILES_PREFIX = "files/"
CHECK_EVERY_SECONDS = 5
RESTORE_ATTEMPTS = 4

_transport: httpx.BaseTransport | None = None     # tests swap in a fake Supabase
_instance = uuid.uuid4().hex
_thread: threading.Thread | None = None
_stop = threading.Event()
_pending_files: set[str] = set()
_lock = threading.Lock()
_lost_lease = False
_files = ThreadPoolExecutor(max_workers=2, thread_name_prefix="uc2-backup-file")
_uploads: set[Future] = set()


class BackupError(RuntimeError):
    pass


def enabled() -> bool:
    return get_settings().backup_enabled


def _client() -> httpx.Client:
    s = get_settings()
    headers = {"apikey": s.backup_key}
    if s.backup_key.startswith("eyJ"):       # a legacy service_role JWT also goes in Authorization; new sb_secret_ keys do not
        headers["Authorization"] = f"Bearer {s.backup_key}"
    return httpx.Client(base_url=f"{s.backup_url}/storage/v1", transport=_transport, timeout=30, headers=headers)


def _missing(r: httpx.Response) -> bool:
    # Supabase answers a missing object with 404, or with 400 and "not found" in the body.
    return r.status_code == 404 or (r.status_code == 400 and "not found" in r.text.lower())


def _get(path: str) -> bytes | None:
    """The object's bytes, None if it does not exist. Raises on any other failure."""
    with _client() as c:
        r = c.get(f"/object/{get_settings().backup_bucket}/{path}")
    if _missing(r):
        return None
    if r.status_code != 200:
        raise BackupError(f"download {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.content


def _put(path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    with _client() as c:
        r = c.post(f"/object/{get_settings().backup_bucket}/{path}", content=data,
                   headers={"x-upsert": "true", "Content-Type": content_type})
    if r.status_code not in (200, 201):
        raise BackupError(f"upload {path}: HTTP {r.status_code} {r.text[:200]}")


def _ensure_bucket() -> None:
    bucket = get_settings().backup_bucket
    with _client() as c:
        r = c.post("/bucket", json={"id": bucket, "name": bucket, "public": False})
    if r.status_code not in (200, 201) and "exist" not in r.text.lower():
        log.warning("could not create bucket %s (HTTP %s); it must already exist", bucket, r.status_code)


# ------------------------------------------------------------------ database

def restore() -> bool:
    """Download the last database copy if there is no database file yet. True when one was restored."""
    db_path = get_settings().db_path
    if db_path.exists():
        return False
    last_error = None
    for attempt in range(RESTORE_ATTEMPTS):
        try:
            _ensure_bucket()
            data = _get(DB_OBJECT)
            break
        except (httpx.HTTPError, BackupError) as e:
            last_error = e
            log.warning("restore attempt %d failed: %s", attempt + 1, e)
            time.sleep(min(2 ** attempt, 8))
    else:
        raise BackupError(f"Could not read the backup, so the server will not start with an empty database: {last_error}")
    if data is None:
        log.info("no backup yet: starting with a new database")
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".restore")
    try:
        tmp.write_bytes(gzip.decompress(data))
        check = sqlite3.connect(tmp)
        try:
            ok = check.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            check.close()
    except (OSError, EOFError, sqlite3.DatabaseError) as e:      # not gzip, cut short, or not a database
        ok = str(e)
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"The backup copy is damaged ({ok}); refusing to start with it.")
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    tmp.replace(db_path)
    log.info("restored the database from the backup (%d bytes)", len(data))
    return True


def _take_lease() -> None:
    _put(LEASE_OBJECT, json.dumps({"instance": _instance, "since": time.time()}).encode(), "application/json")


def _still_ours() -> bool:
    global _lost_lease
    if _lost_lease:
        return False
    raw = _get(LEASE_OBJECT)
    if raw is not None and json.loads(raw).get("instance") != _instance:
        _lost_lease = True
        log.warning("a newer server took over the backup; this one stops uploading")
        return False
    return True


def _snapshot(src: sqlite3.Connection) -> bytes:
    dst = sqlite3.connect(":memory:")
    try:
        src.backup(dst)
        return gzip.compress(dst.serialize(), compresslevel=6)
    finally:
        dst.close()


def upload_db(src: sqlite3.Connection) -> bool:
    if not _still_ours():
        return False
    _put(DB_OBJECT, _snapshot(src), "application/gzip")
    return True


# ------------------------------------------------------------------ files

def _upload_file(key: str, data: bytes) -> None:
    try:
        _put(FILES_PREFIX + key, data)
    except (httpx.HTTPError, BackupError) as e:
        log.warning("file upload failed, will retry: %s", e)
        with _lock:
            _pending_files.add(key)


def put_file(key: str, data: bytes) -> None:
    """Upload a saved file in the background, so a patient's upload never waits on the backup.
    A failure is retried by the background thread."""
    future = _files.submit(_upload_file, key, data)
    with _lock:
        _uploads.add(future)
    future.add_done_callback(lambda f: _uploads.discard(f))


def flush(timeout: float = 20) -> None:
    """Wait for file uploads still in progress."""
    with _lock:
        running = list(_uploads)
    wait(running, timeout=timeout)


def get_file(key: str) -> bytes | None:
    try:
        return _get(FILES_PREFIX + key)
    except (httpx.HTTPError, BackupError) as e:
        log.warning("file download failed: %s", e)
        return None


def delete_file(key: str) -> None:
    with _lock:
        _pending_files.discard(key)
    try:
        with _client() as c:
            c.request("DELETE", f"/object/{get_settings().backup_bucket}", json={"prefixes": [FILES_PREFIX + key]})
    except httpx.HTTPError as e:
        log.warning("file delete failed: %s", e)


def _retry_files() -> None:
    from . import storage
    with _lock:
        keys = list(_pending_files)
        _pending_files.clear()
    for key in keys:
        try:
            put_file(key, storage.read_local(key))
        except FileNotFoundError:
            pass                                    # deleted in the meantime


# ------------------------------------------------------------------ background thread

def _loop(conn: sqlite3.Connection, version: int) -> None:
    while True:
        stopping = _stop.wait(CHECK_EVERY_SECONDS)
        try:
            _retry_files()
            now = conn.execute("PRAGMA data_version").fetchone()[0]
            if now != version and upload_db(conn):
                version = now
        except Exception:                           # noqa: BLE001 - keep trying; never take the server down
            log.exception("backup upload failed; retrying")
        if stopping:
            break
    conn.close()


def start() -> None:
    """Begin watching for changes. Called after migrations and seeding, so start-up writes are not uploaded
    on their own (they are repeated on every start anyway)."""
    global _thread, _lost_lease
    _stop.clear()
    _lost_lease = False
    _take_lease()
    conn = sqlite3.connect(get_settings().db_path, check_same_thread=False, timeout=10)
    version = conn.execute("PRAGMA data_version").fetchone()[0]
    _thread = threading.Thread(target=_loop, args=(conn, version), name="uc2-backup", daemon=True)
    _thread.start()


def stop() -> None:
    """Upload the last changes and stop."""
    global _thread
    if _thread is None:
        return
    flush()
    _stop.set()
    _thread.join(timeout=25)
    _thread = None
