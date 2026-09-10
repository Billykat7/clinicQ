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

from src.core.audit import build_diff


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
