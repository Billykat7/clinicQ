"""Audit core: snapshot serialisation, diff building and sensitive-field redaction (Issue #78).

Essential pure logic worth isolating: that :func:`build_diff` reports only the fields that
changed, that a create/delete carries the right one-sided diff, and — the security-critical part —
that values of sensitive/encrypted fields are **redacted** so the audit trail never becomes a
second plaintext copy of a secret. The DB write path (append-only, atomic with the mutation) is
exercised end-to-end in ``tests/integration/audit/test_audit_api.py``.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from datetime import date

from src.core.audit import REDACTED, build_diff


def test_diff_reports_only_changed_fields() -> None:
    """Unchanged fields are omitted; changed fields carry before and after."""
    before = {"full_name": "Ada", "phone": "111", "email": "a@x.com"}
    after = {"full_name": "Ada Lovelace", "phone": "111", "email": "a@x.com"}

    diff = build_diff(before, after)

    assert diff == {"full_name": {"before": "Ada", "after": "Ada Lovelace"}}


def test_diff_for_create_has_only_after() -> None:
    """A create (no before) yields an after-only diff for each set field."""
    diff = build_diff(None, {"full_name": "New", "phone": None})
    assert diff == {"full_name": {"before": None, "after": "New"}}


def test_diff_for_delete_has_only_before() -> None:
    """A delete (no after) yields a before-only diff."""
    diff = build_diff({"is_deleted": False}, {"is_deleted": True})
    assert diff == {"is_deleted": {"before": False, "after": True}}


def test_diff_values_are_json_safe() -> None:
    """Non-JSON-native column values (dates) are coerced to ISO strings for the JSON diff column."""
    diff = build_diff(
        {"start_date": date(2020, 1, 1)}, {"start_date": date(2021, 6, 30)}
    )
    assert diff["start_date"] == {"before": "2020-01-01", "after": "2021-06-30"}


# ── Issue 20: the diff shows what changed, not a copy of the record ───────────────────────


def test_a_patient_diff_names_the_fields_that_changed_and_none_of_their_values() -> (
    None
):
    """The audit needs to show *that* a patient's contact details changed, never what they are.

    Personal data is redacted by field name (``AUDIT_REDACTED_FIELDS``), so a trail cannot become a
    second copy of a patient record — the criterion Issue 20 calls "minimised to what the audit
    needs". Non-personal fields (a flag, a channel, a timestamp) stay readable, because they are
    what makes the row useful.
    """
    before = {
        "phone_e164": "+27821234567",
        "display_name": "Thandi M",
        "whatsapp_id": "27821234567",
        "last_channel": "ussd",
        "is_deleted": False,
    }
    after = {
        "phone_e164": "+27821234999",
        "display_name": "Thandi Mokoena",
        "whatsapp_id": None,
        "last_channel": "web",
        "is_deleted": True,
    }

    diff = build_diff(before, after)

    assert set(diff) == {
        "phone_e164",
        "display_name",
        "whatsapp_id",
        "last_channel",
        "is_deleted",
    }
    for field in ("phone_e164", "display_name"):
        assert diff[field] == {"before": REDACTED, "after": REDACTED}
    # Cleared: the trail still shows the field was emptied, without ever holding the value.
    assert diff["whatsapp_id"] == {"before": REDACTED, "after": None}
    # And what the audit is for stays legible.
    assert diff["last_channel"] == {"before": "ussd", "after": "web"}
    assert diff["is_deleted"] == {"before": False, "after": True}
    rendered = str(diff)
    for personal in ("+27821234567", "+27821234999", "Thandi", "27821234567"):
        assert personal not in rendered
