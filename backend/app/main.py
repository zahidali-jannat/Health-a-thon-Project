from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .routers import appointments, auth, clinical, portal, reports


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bring the schema up to date. No data is ever created automatically.
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    yield


app = FastAPI(title="UC2 Consultation Readiness", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(clinical.router)
app.include_router(portal.router)
app.include_router(appointments.clinical)
app.include_router(appointments.portal)
app.include_router(reports.router)
