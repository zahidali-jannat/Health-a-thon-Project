"""A read-only FHIR R4-shaped view of a patient's test orders. Built on request from the database (which stays the
source of truth); nothing here is stored. This is FHIR R4 shaped data only - it does not claim ABDM or any national
profile compliance.

  test order item              -> ServiceRequest
  VERIFIED result of an item   -> Observation (status final, basedOn its ServiceRequest)
  report file linked to an item-> DocumentReference (the API link to the file - never a storage path)
Pending or rejected uploads are never Observations.
"""

import uuid
from datetime import datetime, timezone

from . import orders

UCUM = "http://unitsofmeasure.org"
LOINC = "http://loinc.org"
SR_STATUS = {"ordered": "active", "sample_collected": "active", "result_received": "active",
             "submitted_by_patient": "active", "rejected": "active", "verified": "completed", "closed": "completed",
             "cancelled": "revoked", "not_done": "revoked"}


def _instant(text: str | None) -> str | None:
    """Stored UTC timestamps as FHIR dateTime with an explicit offset."""
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _code(r: dict) -> dict:
    name = r["custom_name"] or r["display_name"]
    code = {"text": name}
    if r["loinc_code"] and not r["custom_name"]:
        code["coding"] = [{"system": LOINC, "code": r["loinc_code"], "display": name}]
    return code


def _rows(conn, patient_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(orders._ITEM_SELECT + "WHERE i.patient_id = ? ORDER BY s.created_at, i.due_by",
                                          (patient_id,))]


def service_requests(conn, patient_id: str) -> list[dict]:
    out = []
    for r in _rows(conn, patient_id):
        sr = {"resourceType": "ServiceRequest", "id": r["id"], "status": SR_STATUS[r["status"]], "intent": "order",
              "priority": "urgent" if r["priority"] == "urgent" else "routine", "code": _code(r),
              "subject": {"reference": f"Patient/{patient_id}"}, "authoredOn": _instant(r["created_at"]),
              "occurrencePeriod": {"end": r["due_by"]},
              "requester": {"reference": f"Practitioner/{r['ordered_by_doctor_id']}", "display": r["doctor_name"]}}
        if r["instructions"]:
            sr["patientInstruction"] = r["instructions"]
        if r["status_reason"]:
            sr["note"] = [{"text": f"{orders.LABELS[r['status']]}: {r['status_reason']}"}]
        out.append(sr)
    return out


def observations(conn, patient_id: str) -> list[dict]:
    out = []
    for r in _rows(conn, patient_id):
        if r["status"] not in orders.DONE:            # only verified results
            continue
        if r["lab_value"] is not None:
            value, unit, when, lab = r["lab_value"], r["lab_unit"], r["lab_test_date"], r["lab_lab_name"]
        elif r["up_value"] is not None:
            value, unit, when, lab = r["up_value"], r["up_unit"], r["up_test_date"], r["report_lab_name"] or "Outside lab"
        else:
            continue
        quantity = {"value": value, "unit": unit}
        if r["ucum_unit"] and unit == r["expected_unit"]:
            quantity.update(system=UCUM, code=r["ucum_unit"])
        obs = {"resourceType": "Observation", "id": f"result-{r['id']}", "status": "final",
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": "laboratory", "display": "Laboratory"}]}],
               "code": _code(r), "subject": {"reference": f"Patient/{patient_id}"},
               "basedOn": [{"reference": f"ServiceRequest/{r['id']}"}], "effectiveDateTime": when,
               "valueQuantity": quantity, "performer": [{"display": lab}]}
        if r["linked_report_id"] and r["lab_value"] is None:
            obs["derivedFrom"] = [{"reference": f"DocumentReference/{r['linked_report_id']}"}]
        out.append(obs)
    return out


def document_references(conn, patient_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT x.id, x.status, x.upload_date, x.content_type, x.test_date, x.lab_name, x.test_order_item_id, "
        "COALESCE(i.custom_name, c.display_name) AS test FROM external_reports x "
        "JOIN test_order_items i ON i.id = x.test_order_item_id JOIN test_catalog c ON c.id = i.test_catalog_id "
        "WHERE x.patient_id = ? ORDER BY x.upload_date", (patient_id,)).fetchall()
    out = []
    for x in rows:
        out.append({"resourceType": "DocumentReference", "id": x["id"],
                    "status": "entered-in-error" if x["status"] == "rejected" else "current",
                    "docStatus": "final" if x["status"] == "reviewed" else "preliminary",
                    "subject": {"reference": f"Patient/{patient_id}"}, "date": _instant(x["upload_date"]),
                    "description": f"{x['test']} report from {x['lab_name'] or 'an outside lab'} (test date {x['test_date']})",
                    "content": [{"attachment": {"contentType": x["content_type"], "title": f"{x['test']} report",
                                                "creation": _instant(x["upload_date"]),
                                                "url": f"/api/patients/{patient_id}/external-reports/{x['id']}/file"}}],
                    "context": {"related": [{"reference": f"ServiceRequest/{x['test_order_item_id']}"}]}})
    return out


def bundle(conn, patient_id: str) -> dict:
    entries = [*service_requests(conn, patient_id), *observations(conn, patient_id), *document_references(conn, patient_id)]
    return {"resourceType": "Bundle", "type": "collection", "timestamp": _instant(datetime.now(timezone.utc).isoformat()),
            "entry": [{"fullUrl": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, e['resourceType'] + '/' + e['id'])}",
                       "resource": e} for e in entries]}
