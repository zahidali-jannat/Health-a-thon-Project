"""Test Orders API.

Care team (/api/...): every patient route depends on patient_for_clinician (only the patient's care team);
item routes check the same through the item's patient. Patient (/api/me/...): the patient comes from the session
only. Clinic lab system (/api/internal/lab-results): a service credential, never a user session.
Errors: {"detail": {"code": ..., "message": ..., ...}}.
"""

import hashlib
import hmac
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import fhir, repo
from .. import orders
from ..deps import current_clinician, current_patient, get_conn, patient_for_clinician, today
from ..settings import get_settings

clinical = APIRouter(prefix="/api", tags=["test orders (care team)"])
portal = APIRouter(prefix="/api/me", tags=["test orders (patient)"])
internal = APIRouter(prefix="/api/internal", tags=["clinic lab system"])

Priority = Literal["routine", "urgent"]
Route = Literal["clinic_lab", "external", "either"]
ALLOWED_FILES = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}


def _fail(e: orders.OrderError) -> JSONResponse:
    return JSONResponse({"detail": {"code": e.code, "message": e.message, **(e.payload or {})}}, status_code=e.status)


def _idem(scope: str, key: str | None, route: str, payload) -> tuple | None:
    if not key:
        return None
    if not 8 <= len(key) <= 100:
        raise HTTPException(422, {"code": "bad_idempotency_key", "message": "Idempotency-Key must be 8 to 100 characters."})
    return scope, key, route, orders.request_hash(payload)


# ================================================================== care team

class OrderItemIn(BaseModel):
    test_catalog_id: int
    custom_name: str | None = Field(default=None, max_length=80)
    due_by: date | None = None
    instructions: str | None = Field(default=None, max_length=500)
    priority: Priority = "routine"
    fulfilment_route: Route = "either"


class OrderIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)
    ordered_by_doctor_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    acknowledge_warnings: bool = False


@clinical.get("/test-catalog")
def test_catalog(user=Depends(current_clinician), conn=Depends(get_conn)):
    return {"tests": orders.catalog(conn), "demo_mode": get_settings().demo_mode,
            "buffer_days": orders.config()["due_buffer_days"]}


@clinical.get("/patients/{patient_id}/test-orders")
def patient_test_orders(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                        patient=Depends(patient_for_clinician), conn=Depends(get_conn)):
    return orders.patient_orders(conn, patient["id"], today(), page, page_size)


@clinical.post("/patients/{patient_id}/test-orders/preview")
def preview_test_order(body: OrderIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                       conn=Depends(get_conn)):
    """The confirmation summary: due dates filled in, errors and warnings - nothing is saved."""
    return orders.plan_order(conn, patient["id"], [i.model_dump() for i in body.items], today())


@clinical.post("/patients/{patient_id}/test-orders", status_code=201)
def create_test_order(body: OrderIn, idempotency_key: str | None = Header(default=None),
                      patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    payload = body.model_dump(mode="json")
    try:
        status, response = orders.create_order(conn, patient, user, {**payload, "items": [i.model_dump() for i in body.items]},
                                               today(), _idem(f"clinician:{user['id']}", idempotency_key,
                                                              f"POST /patients/{patient['id']}/test-orders", payload))
    except orders.OrderError as e:
        return _fail(e)
    return JSONResponse(response, status_code=status)


class ItemEditIn(BaseModel):
    version: int
    due_by: date | None = None
    instructions: str | None = Field(default=None, max_length=500)
    priority: Priority | None = None
    fulfilment_route: Route | None = None
    reason: str | None = Field(default=None, max_length=200)


class ItemReasonIn(BaseModel):
    version: int
    reason: str = Field(max_length=200)


class ItemVersionIn(BaseModel):
    version: int


def _item(conn, item_id: str, user: dict) -> dict:
    return orders.item_for_clinician(conn, item_id, user["id"])


@clinical.patch("/test-order-items/{item_id}")
def edit_test_order_item(item_id: str, body: ItemEditIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    try:
        warnings = orders.edit_item(conn, _item(conn, item_id, user), changes, body.version, user, today())
    except orders.OrderError as e:
        return _fail(e)
    return {"ok": True, "warnings": warnings}


def _status_change(conn, item_id: str, user: dict, to: str, version: int, reason: str | None, why: str):
    try:
        orders.change_status(conn, _item(conn, item_id, user), to, version, user, reason, why)
    except orders.OrderError as e:
        return _fail(e)
    return {"ok": True, "status": to}


@clinical.post("/test-order-items/{item_id}/cancel")
def cancel_test_order_item(item_id: str, body: ItemReasonIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    return _status_change(conn, item_id, user, "cancelled", body.version, body.reason, "Cancelled by the care team.")


@clinical.post("/test-order-items/{item_id}/waive")
def waive_test_order_item(item_id: str, body: ItemReasonIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    return _status_change(conn, item_id, user, "not_done", body.version, body.reason, "Waived by the care team.")


@clinical.post("/test-order-items/{item_id}/close")
def close_test_order_item(item_id: str, body: ItemVersionIn, user=Depends(current_clinician), conn=Depends(get_conn)):
    return _status_change(conn, item_id, user, "closed", body.version, None, "Closed after the result was discussed.")


@clinical.get("/clinic/tests/overdue")
def overdue_tests(doctor_id: str | None = None, within_days: int | None = Query(None, ge=0, le=365),
                  page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                  user=Depends(current_clinician), conn=Depends(get_conn)):
    return orders.overdue_tests(conn, user["id"], today(), doctor_id, within_days, page, page_size)


class AcceptIn(BaseModel):
    test_order_item_id: str | None = Field(default=None, max_length=64)


class RejectIn(BaseModel):
    reason: str = Field(max_length=200)


@clinical.post("/patients/{patient_id}/lab-results/{ingestion_id}/accept")
def accept_lab_result(ingestion_id: str, body: AcceptIn, patient=Depends(patient_for_clinician),
                      user=Depends(current_clinician), conn=Depends(get_conn)):
    try:
        orders.accept_ingestion(conn, patient["id"], ingestion_id, user, body.test_order_item_id, today())
    except orders.OrderError as e:
        return _fail(e)
    return {"ok": True}


@clinical.post("/patients/{patient_id}/lab-results/{ingestion_id}/reject")
def reject_lab_result(ingestion_id: str, body: RejectIn, patient=Depends(patient_for_clinician),
                      user=Depends(current_clinician), conn=Depends(get_conn)):
    try:
        orders.reject_ingestion(conn, patient["id"], ingestion_id, user, body.reason)
    except orders.OrderError as e:
        return _fail(e)
    return {"ok": True}


class DemoResultIn(BaseModel):
    test_order_item_id: str = Field(max_length=64)
    value: float
    test_date: date | None = None


@clinical.post("/patients/{patient_id}/test-orders/demo-lab-result")
def demo_lab_result(body: DemoResultIn, patient=Depends(patient_for_clinician), user=Depends(current_clinician),
                    conn=Depends(get_conn)):
    """Demo simulator only: sends a made-up clinic-lab result through the SAME ingestion path as the lab system.
    Unavailable unless demo mode is on (never outside the dev environment)."""
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not found.")
    item = orders._load_item(conn, body.test_order_item_id, patient["id"])
    if item is None:
        raise HTTPException(404, "Test order not found.")
    repo.audit(conn, repo.clinician_actor(user), "DEMO_LAB_RESULT_SIMULATED", patient_id=patient["id"],
               resource_type="test_order_item", resource_id=item["id"], detail="Demo simulator used")
    conn.commit()
    try:
        return orders.ingest_lab_result(conn, {"lab_order_ref": f"DEMO-{repo.new_id()}", "test_code": item["code"],
                                               "patient_id": patient["id"], "value": body.value, "unit": item["expected_unit"],
                                               "test_date": (body.test_date or today()).isoformat()}, today())
    except orders.OrderError as e:
        return _fail(e)


# ------------------------------------------------------------------ FHIR (read-only, clinician only, audited)

def _fhir(conn, patient: dict, user: dict, what: str):
    repo.audit(conn, repo.clinician_actor(user), "FHIR_VIEWED", patient_id=patient["id"], resource_type="fhir", detail=what)
    conn.commit()


def _searchset(resources: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "total": len(resources),
            "entry": [{"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r, "search": {"mode": "match"}} for r in resources]}


@clinical.get("/patients/{patient_id}/fhir/ServiceRequest")
def fhir_service_requests(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    _fhir(conn, patient, user, "ServiceRequest")
    return _searchset(fhir.service_requests(conn, patient["id"]))


@clinical.get("/patients/{patient_id}/fhir/Observation")
def fhir_observations(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    _fhir(conn, patient, user, "Observation")
    return _searchset(fhir.observations(conn, patient["id"]))


@clinical.get("/patients/{patient_id}/fhir/DocumentReference")
def fhir_documents(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    _fhir(conn, patient, user, "DocumentReference")
    return _searchset(fhir.document_references(conn, patient["id"]))


@clinical.get("/patients/{patient_id}/fhir/Bundle")
def fhir_bundle(patient=Depends(patient_for_clinician), user=Depends(current_clinician), conn=Depends(get_conn)):
    _fhir(conn, patient, user, "Bundle")
    return JSONResponse(fhir.bundle(conn, patient["id"]), media_type="application/fhir+json")


# ================================================================== patient

@portal.get("/test-orders")
def my_test_orders(patient=Depends(current_patient), conn=Depends(get_conn)):
    return orders.my_tests(conn, patient["id"], today())


async def _read_report(file: UploadFile) -> tuple[bytes, str]:
    """PDF, JPG or PNG only, checked by the file's own first bytes (not its name or the browser's claim)."""
    limit = int(orders.config()["max_upload_mb"]) * 1024 * 1024
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(422, {"code": "empty_file", "message": "The file is empty."})
    if len(data) > limit:
        raise HTTPException(413, {"code": "too_large", "message": f"Please use a file under {limit // (1024 * 1024)} MB."})
    kind = next((t for t, magic in ALLOWED_FILES.items() if data.startswith(magic)), None)
    if kind is None:
        raise HTTPException(415, {"code": "bad_file_type", "message": "Please upload a PDF, JPG or PNG file."})
    return data, kind


@portal.post("/test-orders/{item_id}/upload", status_code=201)
async def upload_test_report(item_id: str, file: UploadFile = File(...), test_date: date = Form(...),
                             lab_name: str = Form(..., max_length=120), custom_name: str | None = Form(None, max_length=80),
                             idempotency_key: str | None = Header(default=None),
                             patient=Depends(current_patient), conn=Depends(get_conn)):
    data, kind = await _read_report(file)
    idem = _idem(f"patient:{patient['id']}", idempotency_key, f"POST /me/test-orders/{item_id}/upload",
                 {"test_date": test_date.isoformat(), "lab_name": lab_name, "custom_name": custom_name,
                  "file": hashlib.sha256(data).hexdigest()})
    try:
        status, response = orders.upload_for_item(conn, patient, item_id, custom_name=custom_name, test_date=test_date,
                                                  lab_name=lab_name, file_name=(file.filename or "report")[:120],
                                                  content_type=kind, data=data, today=today(), idem=idem)
    except orders.OrderError as e:
        return _fail(e)
    return JSONResponse(response, status_code=status)


# ================================================================== clinic lab system

class LabResultIn(BaseModel):
    lab_order_ref: str = Field(min_length=1, max_length=64)
    test_code: str = Field(min_length=1, max_length=20)
    patient_id: str | None = Field(default=None, max_length=64)
    patient_code: str | None = Field(default=None, max_length=20)
    value: float
    unit: str | None = Field(default=None, max_length=30)
    test_date: date
    lab_name: str | None = Field(default=None, max_length=120)


def service_credential(x_service_token: str | None = Header(default=None)) -> None:
    expected = get_settings().lab_service_token
    if not expected:
        raise HTTPException(503, {"code": "not_configured", "message": "The lab result link is not configured."})
    if not x_service_token or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(401, {"code": "bad_credential", "message": "Invalid service credential."})


@internal.post("/lab-results", dependencies=[Depends(service_credential)])
def receive_lab_result(body: LabResultIn, conn=Depends(get_conn)):
    if not (body.patient_id or body.patient_code):
        raise HTTPException(422, {"code": "patient_required", "message": "Send patient_id or patient_code."})
    try:
        return orders.ingest_lab_result(conn, body.model_dump(mode="json"), today())
    except orders.OrderError as e:
        return _fail(e)
