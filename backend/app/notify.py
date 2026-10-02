"""Notifications to patients.

In-app messages are real (patient_notifications, shown in the patient app). SMS and WhatsApp are stubs: nothing is
sent, and the log records only that a message WOULD have gone out - its notification id and channel, never the text
(it names tests) and never anything else about the patient's health.
"""

import logging

from .db import now_iso

log = logging.getLogger("uc2.notify")
STUB_CHANNELS = ("sms", "whatsapp")


def notify_patient(conn, patient_id: str, kind: str, message: str, *, test_order_item_id: str | None = None,
                   channels: tuple[str, ...] = ("in_app", *STUB_CHANNELS)) -> int:
    """Records the in-app message (the caller's transaction) and hands it to the stub channels. Returns its id."""
    nid = conn.execute(
        "INSERT INTO patient_notifications (patient_id, kind, test_order_item_id, message, email_to, email_status, created_at) "
        "VALUES (?, ?, ?, ?, NULL, 'no_email_on_file', ?)", (patient_id, kind, test_order_item_id, message, now_iso())).lastrowid
    for channel in channels:
        if channel in STUB_CHANNELS:
            log.info("%s stub: notification %s queued (not sent - no provider connected)", channel, nid)
    return nid
