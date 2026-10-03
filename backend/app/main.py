import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from . import db
from . import orders
from .routers import appointments, auth, briefs, clinical, portal, reports
from .routers import orders as test_order_routes
from .settings import get_settings

log = logging.getLogger("uc2.reminders")
REMINDER_EVERY_SECONDS = 60 * 60


async def _reminder_loop() -> None:
    """Test-order reminders, hourly. Safe to run any number of times: each reminder is sent at most once."""
    while True:
        try:
            conn = db.connect()
            try:
                sent = await asyncio.to_thread(orders.run_reminders, conn, orders.today_in_clinic())
            finally:
                conn.close()
            if sent:
                log.info("test-order reminders sent: %s", sent)
        except Exception:                      # noqa: BLE001 - one failed run must not stop the next
            log.exception("test-order reminder run failed")
        await asyncio.sleep(REMINDER_EVERY_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bring the schema up to date and the test catalog in line with backend/config/test_orders.json.
    # No patient data is ever created automatically.
    conn = db.connect()
    db.migrate(conn)
    orders.sync_catalog(conn)
    conn.commit()
    if get_settings().seed_demo and not conn.execute("SELECT 1 FROM patients LIMIT 1").fetchone():
        # A public demo only (UC2_SEED_DEMO=1): made-up patients, never a real database.
        from .seed import seed_database
        seed_database(conn, date.today(), brief_demo=True)
        log.info("public demo: loaded the demo fixtures")
    conn.close()
    task = asyncio.create_task(_reminder_loop()) if get_settings().reminders else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="UC2 Consultation Readiness", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(clinical.router)
app.include_router(portal.router)
app.include_router(appointments.clinical)
app.include_router(appointments.portal)
app.include_router(reports.router)
app.include_router(test_order_routes.clinical)
app.include_router(test_order_routes.portal)
app.include_router(test_order_routes.internal)
app.include_router(briefs.router)


# ------------------------------------------------------------------ the website, robots.txt and sitemap

# Only the public front page is meant for search engines. Signed-in areas and the API are never indexed.
PRIVATE_PREFIXES = ("/api", "/care-team", "/doctor", "/patient")
PUBLIC_PAGES = ("/",)


@app.middleware("http")
async def no_index_private(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(PRIVATE_PREFIXES):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots_txt() -> PlainTextResponse:
    site = get_settings().site_url
    lines = ["User-agent: *", "Allow: /$", *(f"Disallow: {p}" for p in PRIVATE_PREFIXES)]
    if site:
        lines.append(f"Sitemap: {site}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n")


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_xml() -> Response:
    site = get_settings().site_url
    if not site:
        raise HTTPException(404, "Set SITE_URL to publish a sitemap.")
    urls = "".join(f"<url><loc>{site}{p}</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>"
                   for p in PUBLIC_PAGES)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, media_type="application/xml")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def website(path: str):
    """The built React app (frontend/dist), when present: real files as they are, every other page -> index.html."""
    dist = get_settings().frontend_dist
    if path.startswith("api/") or path == "api" or dist is None or not (dist / "index.html").is_file():
        raise HTTPException(404, "Not found.")
    file = (dist / path).resolve()
    if path and file.is_file() and file.is_relative_to(dist.resolve()):
        cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "public, max-age=3600"
        return FileResponse(file, headers={"Cache-Control": cache})
    return HTMLResponse(_index_html(dist / "index.html"), headers={"Cache-Control": "no-cache"})


def _index_html(index: Path) -> str:
    """index.html with the public address filled in (canonical link, share preview, structured data).
    Without SITE_URL the address-bearing tags are left out rather than pointing nowhere."""
    html, site = index.read_text(encoding="utf-8"), get_settings().site_url
    if site:
        return html.replace("__SITE_URL__", site)
    keep = [line for line in html.splitlines()
            if "__SITE_URL__" not in line or not line.strip().startswith(("<link", "<meta", "<!--"))]
    return "\n".join(keep).replace("__SITE_URL__", "")
