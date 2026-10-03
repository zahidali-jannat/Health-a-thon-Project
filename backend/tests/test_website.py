"""Hosting: the API serves the built website, search engines see only the public front page, and a public demo
loads made-up patients into an empty database (never into one that has patients)."""

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text('<!doctype html><title>UC2 Consultation Readiness</title>\n'
                                     '    <link rel="canonical" href="__SITE_URL__/" />\n'
                                     '    <script type="application/ld+json">{"url": "__SITE_URL__/"}</script>')
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.main import app
    return TestClient(app)


def test_the_website_and_its_deep_links_are_served(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        home = c.get("/")
        assert home.status_code == 200 and "UC2 Consultation Readiness" in home.text
        assert "X-Robots-Tag" not in home.headers                          # the front page may be indexed
        assert "__SITE_URL__" not in home.text and "canonical" not in home.text   # no address set: no canonical link
        assert c.head("/").status_code == 200 and c.head("/robots.txt").status_code == 200   # crawlers, uptime checks
        deep = c.get("/care-team/patients")                                  # a React route, not a file
        assert deep.status_code == 200 and deep.headers["X-Robots-Tag"] == "noindex, nofollow"
        js = c.get("/assets/app.js")
        assert js.status_code == 200 and "immutable" in js.headers["Cache-Control"]
        assert c.get("/api/no-such-thing").status_code == 404                # the API never falls back to the page
        assert c.get("/api/auth/clinician/me").headers["X-Robots-Tag"] == "noindex, nofollow"
        for probe in ("/..%2F..%2F..%2Fbackend%2Fapp%2Fmain.py", "/%2e%2e/%2e%2e/%2e%2e/backend/app/main.py"):
            assert "FastAPI" not in c.get(probe).text                        # nothing outside the build folder


def test_robots_and_sitemap(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/sitemap.xml").status_code == 404                      # no public address configured yet
        robots = c.get("/robots.txt").text
        assert "Disallow: /care-team" in robots and "Disallow: /patient" in robots and "Sitemap" not in robots
    monkeypatch.setenv("SITE_URL", "https://uc2.example.org/")
    with _client(monkeypatch, tmp_path) as c:
        assert "Sitemap: https://uc2.example.org/sitemap.xml" in c.get("/robots.txt").text
        assert '<link rel="canonical" href="https://uc2.example.org/" />' in c.get("/").text
        sm = c.get("/sitemap.xml")
        assert sm.headers["content-type"].startswith("application/xml") and "<loc>https://uc2.example.org/</loc>" in sm.text


def test_public_demo_loads_made_up_patients_only_into_an_empty_database(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, UC2_SEED_DEMO="1") as c:
        r = c.post("/api/auth/clinician/login", json={"identifier": "CLN-TNSQ01", "password": "demo1234"})
        assert r.status_code == 200 and r.json()["role"] == "care_team"
        assert len(c.get("/api/patients").json()["patients"]) == 4
    with _client(monkeypatch, tmp_path, UC2_SEED_DEMO="1") as c:              # a restart never loads them twice
        c.post("/api/auth/clinician/login", json={"identifier": "CLN-TNSQ01", "password": "demo1234"})
        assert len(c.get("/api/patients").json()["patients"]) == 4
