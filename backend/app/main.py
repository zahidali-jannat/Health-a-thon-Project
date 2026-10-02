import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

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
