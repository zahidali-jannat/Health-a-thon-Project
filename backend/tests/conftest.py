import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

TODAY = date(2026, 9, 25)
PRIYA = "CLN-PRYA27"      # care team for all three demo patients
KARAN = "CLN-KRNB58"      # real account, no patients
PASSWORD = "demo1234"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own database file and file store."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("UC2_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("UC2_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("DEMO_TODAY", TODAY.isoformat())
    from app import ratelimit
    ratelimit.reset_all()         # the lockout is in-memory; don't let one test lock out the next
    yield


@pytest.fixture()
def demo_ids():
    """A fresh test database loaded with the development fixtures (the app itself never auto-loads them)."""
    from app import db
    from app.seed import seed_database
    conn = db.reset(os.environ["UC2_DB_PATH"])
    ids = seed_database(conn, TODAY)
    conn.close()
    return ids


@pytest.fixture()
def seeded_conn(demo_ids):
    from app import db
    conn = db.connect(os.environ["UC2_DB_PATH"])
    yield conn, demo_ids
    conn.close()


@pytest.fixture()
def empty_client():
    """A client against a brand-new, EMPTY database - exactly what a real first start looks like."""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_client(demo_ids):
    """Returns a factory: each call is a separate browser (own cookies) against the same seeded app."""
    from app.main import app
    clients = []

    def factory():
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def login_clinician(client, identifier=PRIYA, password=PASSWORD):
    r = client.post("/api/auth/clinician/login", json={"identifier": identifier, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def login_patient(client, identifier):
    code = client.post("/api/auth/patient/request-code", json={"identifier": identifier}).json()["dev_code"]
    r = client.post("/api/auth/patient/verify", json={"identifier": identifier, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def patient_ids(client) -> dict[str, str]:
    """{patient_code: id} for the clinician logged in on this client."""
    return {p["patient_code"]: p["id"] for p in client.get("/api/patients").json()["patients"]}


@pytest.fixture()
def priya(make_client):
    c = make_client()
    login_clinician(c)
    return c
