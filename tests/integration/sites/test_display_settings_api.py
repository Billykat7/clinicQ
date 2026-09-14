"""Changing what a waiting-room board may show, over HTTP (Issue 27, non-negotiable 4).

Every acceptance criterion the guard test does not already cover:

* only a clinic manager may change it; a receptionist reads it and is refused the change;
* switching to a mode that reveals a name is **refused without an explicit confirmation**, so a
  client that renders no warning cannot make the change;
* the warning says, in plain language, what will appear on a public screen;
* a reason beside a **full** name takes a second, separate confirmation;
* the retention window is validated against the policy ceiling;
* every change writes an audit row saying which fields moved.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from starlette import status

from src.commons.enums import AuditAction, AuditEntityType, BoardTheme, DisplayMode
from src.database.models import AuditEvent
from src.modules.sites.settings import (
    COMMENT_WITH_FULL_NAME_WARNING,
    REASON_RETENTION_CEILING_DAYS,
)

_BASE = {
    "display_mode": DisplayMode.NUMBER_ONLY.value,
    "display_show_comment": False,
    "board_language": "en",
    "announce_audio": True,
    "reason_retention_days": 30,
}


def _settings(clinics: SimpleNamespace, **overrides: object) -> dict[str, object]:
    """A full settings payload with fields replaced."""
    return {**_BASE, **overrides}


def _manager(clinics: SimpleNamespace):
    """The clinic manager at Clinic A."""
    return clinics.client("manager.a@clinicq.example")


def _path(clinics: SimpleNamespace) -> str:
    """Clinic A's display settings."""
    return f"/api/v1/sites/{clinics.site_a}/settings/display"


def test_a_new_clinic_reads_back_as_number_only(clinics: SimpleNamespace) -> None:
    """The default, through the API a screen actually calls."""
    current = _manager(clinics).get(_path(clinics))

    assert current.status_code == status.HTTP_200_OK
    body = current.json()
    assert body["display_mode"] == DisplayMode.NUMBER_ONLY.value
    assert body["display_show_comment"] is False
    assert body["reason_retention_ceiling_days"] == REASON_RETENTION_CEILING_DAYS
    assert "ticket numbers only" in body["warning_lines"][0]


def test_switching_to_full_without_confirming_is_refused_and_says_what_would_appear(
    clinics: SimpleNamespace,
) -> None:
    """A client that renders no warning cannot make the change, because the server asks for it."""
    refused = _manager(clinics).put(
        _path(clinics), json=_settings(clinics, display_mode=DisplayMode.FULL.value)
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    detail = refused.json()["detail"]
    assert "full name" in detail
    assert "Anyone in the waiting room" in detail
    # And nothing moved.
    assert (
        _manager(clinics).get(_path(clinics)).json()["display_mode"]
        == DisplayMode.NUMBER_ONLY.value
    )


def test_switching_to_full_with_the_confirmation_works_and_is_audited(
    clinics: SimpleNamespace,
) -> None:
    """The whole criterion in one test: a warning, a confirmation, and an audit row."""
    changed = _manager(clinics).put(
        _path(clinics),
        json=_settings(
            clinics,
            display_mode=DisplayMode.FULL.value,
            confirm_public_display=True,
        ),
    )

    assert changed.status_code == status.HTTP_200_OK, changed.text
    assert changed.json()["display_mode"] == DisplayMode.FULL.value
    with clinics.session() as db:
        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.SITE.value,
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ).scalar_one()
    assert row.actor == "manager.a@clinicq.example"
    assert row.site_id == clinics.site_a
    assert "display_mode: number_only -> full" in (row.context or "")


def test_name_lite_needs_the_same_confirmation_as_full(
    clinics: SimpleNamespace,
) -> None:
    """A first name and an initial is still a name on a public screen."""
    manager = _manager(clinics)
    assert (
        manager.put(
            _path(clinics),
            json=_settings(clinics, display_mode=DisplayMode.NAME_LITE.value),
        ).status_code
        == status.HTTP_409_CONFLICT
    )
    assert (
        manager.put(
            _path(clinics),
            json=_settings(
                clinics,
                display_mode=DisplayMode.NAME_LITE.value,
                confirm_public_display=True,
            ),
        ).status_code
        == status.HTTP_200_OK
    )


def test_a_reason_beside_a_full_name_takes_a_second_separate_confirmation(
    clinics: SimpleNamespace,
) -> None:
    """The sharpest combination the product can produce, so agreeing to it is its own act.

    Per-visit consent still gates the rendering itself (Issue 58); this is the standing decision
    that the screen may ever do it.
    """
    manager = _manager(clinics)
    payload = _settings(
        clinics,
        display_mode=DisplayMode.FULL.value,
        display_show_comment=True,
        confirm_public_display=True,
    )

    refused = manager.put(_path(clinics), json=payload)

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["detail"] == COMMENT_WITH_FULL_NAME_WARNING

    allowed = manager.put(
        _path(clinics), json={**payload, "confirm_comment_with_full_name": True}
    )
    assert allowed.status_code == status.HTTP_200_OK
    body = allowed.json()
    assert body["display_show_comment"] is True
    assert any("health information" in line for line in body["warning_lines"])


def test_going_back_to_number_only_needs_no_confirmation(
    clinics: SimpleNamespace,
) -> None:
    """The safe direction never needs permission to travel in."""
    manager = _manager(clinics)
    manager.put(
        _path(clinics),
        json=_settings(
            clinics,
            display_mode=DisplayMode.FULL.value,
            confirm_public_display=True,
        ),
    )

    back = manager.put(_path(clinics), json=_settings(clinics))

    assert back.status_code == status.HTTP_200_OK
    assert back.json()["display_mode"] == DisplayMode.NUMBER_ONLY.value


def test_a_receptionist_reads_the_setting_and_cannot_change_it(
    clinics: SimpleNamespace,
) -> None:
    """``sites.display`` is its own resource so the front desk can see the board's mode."""
    desk = clinics.client("desk.a@clinicq.example")

    assert desk.get(_path(clinics)).status_code == status.HTTP_200_OK
    assert (
        desk.put(_path(clinics), json=_settings(clinics)).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_a_retention_window_above_the_policy_ceiling_is_refused(
    clinics: SimpleNamespace,
) -> None:
    """A clinic cannot raise the platform's ceiling, and the refusal says so."""
    refused = _manager(clinics).put(
        _path(clinics),
        json=_settings(
            clinics, reason_retention_days=REASON_RETENTION_CEILING_DAYS + 1
        ),
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert str(REASON_RETENTION_CEILING_DAYS) in refused.text


def test_the_ceiling_itself_is_accepted(clinics: SimpleNamespace) -> None:
    """The bound is inclusive, so the refusal above is about the ceiling and not an off-by-one."""
    accepted = _manager(clinics).put(
        _path(clinics),
        json=_settings(clinics, reason_retention_days=REASON_RETENTION_CEILING_DAYS),
    )
    assert accepted.status_code == status.HTTP_200_OK
    assert accepted.json()["reason_retention_days"] == REASON_RETENTION_CEILING_DAYS


def test_the_options_endpoint_serves_the_same_words_the_server_enforces(
    clinics: SimpleNamespace,
) -> None:
    """The screen does not get to write its own reassurance about what a mode does.

    Asked for **as the clinic manager**, because they are the only role that opens the settings
    page. An earlier version of this endpoint had no site in its path, so it needed a
    business-tier grant and 403'd for exactly that role; the page rendered and said "the settings
    could not be loaded". The test asks as the manager so that cannot come back.
    """
    options = _manager(clinics).get(
        f"/api/v1/sites/{clinics.site_a}/settings/display-options"
    )

    assert options.status_code == status.HTTP_200_OK
    body = options.json()
    by_mode = {item["value"]: item for item in body["modes"]}
    assert set(by_mode) == {mode.value for mode in DisplayMode}
    assert by_mode["number_only"]["requires_confirmation"] is False
    assert by_mode["name_lite"]["requires_confirmation"] is True
    assert by_mode["full"]["requires_confirmation"] is True
    assert "full name" in by_mode["full"]["warning"]
    assert body["retention_ceiling_days"] == REASON_RETENTION_CEILING_DAYS
    assert len(body["languages"]) == 11


def test_another_clinics_display_settings_are_not_found(
    clinics: SimpleNamespace,
) -> None:
    """Non-negotiable 3 holds here too, for the read and for the write."""
    manager = _manager(clinics)
    other = f"/api/v1/sites/{clinics.site_b}/settings/display"
    assert manager.get(other).status_code == status.HTTP_404_NOT_FOUND
    assert (
        manager.put(other, json=_settings(clinics)).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_an_unchanged_save_writes_no_audit_row(clinics: SimpleNamespace) -> None:
    """A trail full of "nothing changed" is a trail nobody reads."""
    _manager(clinics).put(_path(clinics), json=_settings(clinics))
    with clinics.session() as db:
        assert db.execute(select(AuditEvent)).scalars().all() == []


def test_the_options_endpoint_is_reachable_by_the_role_that_opens_the_page(
    clinics: SimpleNamespace,
) -> None:
    """The regression: a clinic manager's role is held at a site and resolves only on a site route."""
    for email in ("manager.a@clinicq.example", "desk.a@clinicq.example"):
        answered = clinics.client(email).get(
            f"/api/v1/sites/{clinics.site_a}/settings/display-options"
        )
        assert answered.status_code == status.HTTP_200_OK, email


def test_a_manager_chooses_the_board_theme_with_no_confirmation_and_it_is_audited(
    clinics: SimpleNamespace,
) -> None:
    """A theme changes colours, never what the board says about anyone: no confirmation, one audit row.

    Every clinic starts on ``dim``, the options name and describe each theme, and a receptionist may read
    the choice and cannot change it (Issue 59).
    """
    manager = _manager(clinics)
    assert manager.get(_path(clinics)).json()["board_theme"] == BoardTheme.DIM.value
    options = manager.get(
        f"/api/v1/sites/{clinics.site_a}/settings/display-options"
    ).json()
    assert [theme["value"] for theme in options["themes"]] == [
        t.value for t in BoardTheme
    ]
    assert all(theme["label"] and theme["description"] for theme in options["themes"])

    changed = manager.put(
        _path(clinics),
        json=_settings(clinics, board_theme=BoardTheme.HIGH_CONTRAST.value),
    )
    assert changed.status_code == status.HTTP_200_OK, changed.text
    assert changed.json()["board_theme"] == BoardTheme.HIGH_CONTRAST.value
    with clinics.session() as db:
        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.SITE.value,
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ).scalar_one()
    assert row.context == "display settings: board_theme: dim -> high_contrast"

    refused = clinics.client("desk.a@clinicq.example").put(
        _path(clinics), json=_settings(clinics, board_theme=BoardTheme.BRIGHT.value)
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    unknown = manager.put(_path(clinics), json=_settings(clinics, board_theme="neon"))
    assert unknown.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
