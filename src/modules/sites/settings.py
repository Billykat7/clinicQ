"""What a clinic's waiting-room board may show, and what it takes to change it (Issue 27).

This is where **non-negotiable 4** (``docs/guideline.md``) lives:

    Under ``number_only``, a patient name is not in the response payload at all, not merely hidden
    by CSS. A comment never renders beside a full name without recorded per-visit consent. Every
    new site is created with ``display_mode = number_only``, and no code path may change that
    default.

The first sentence is Issue 58's (the board's server-side projection). The third is this module's,
and it is held in the cheapest possible way: the column's default is
:data:`~src.commons.enums.SITE_DEFAULT_DISPLAY_MODE` and **nothing else in the codebase names a
display mode at creation time**, which ``tests/unit/sites/test_display_defaults.py`` proves by
walking the source of every path that makes a ``Site``.

The middle sentence is why changing the mode is not an ordinary ``PUT``:

* **only a clinic manager may**, through the ``sites.display`` grant;
* **switching to a mode that reveals a name needs an explicit confirmation** in the request, not
  merely a well-behaved screen. A manager who has not seen the warning cannot accidentally agree
  to it, and an API client cannot skip it;
* **the warning says, in plain language, what will appear on a public screen** — the sentences are
  here, next to the rule, so the wording cannot drift away from what the setting actually does;
* **every change writes an audit row** naming the manager, the clinic, and what moved.

Showing a comment beside a **full** name is the sharpest combination in the product — a stated
symptom next to an identifiable person, on a screen a waiting room can read — so it takes a second,
separate confirmation of its own, and it is still gated per visit at render time (Issue 58).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.commons.enums import (
    NAME_REVEALING_DISPLAY_MODES,
    SITE_DEFAULT_ANNOUNCE_VOLUME,
    SITE_DEFAULT_BOARD_THEME,
    BoardLanguage,
    BoardTheme,
    DisplayMode,
)
from src.database.models.site import Site

# --------------------------------------------------------------------------------------
# The retention ceiling (Issue 27, to be replaced by Issue 95)
# --------------------------------------------------------------------------------------
#
# ``reason_text`` is what a patient typed about why they are here: health information, in their own
# words, kept only so staff can triage the visit. The M13 data map (Issue 95) will set the policy
# ceiling; until it does, this issue has to choose one rather than leave the field unbounded.
#
# **The interim ceiling is 90 days**, and the reasoning is written down so Issue 95 can argue with
# it rather than guess at it: POPIA s 14 says personal information may not be kept longer than is
# necessary for the purpose it was collected for, the purpose here is one visit, and the longest
# operational use anyone has named for it is a quarter's worth of clinic-level review. Ninety days
# covers that with nothing left over. The default is far shorter still.

#: The longest a clinic may keep a visit's ``reason_text``. Interim (Issue 27); Issue 95 sets the
#: policy ceiling, and this constant is what it changes.
REASON_RETENTION_CEILING_DAYS = 90
#: The shortest. A day, because anything less would delete a reason before the visit it describes.
REASON_RETENTION_FLOOR_DAYS = 1
#: What a new clinic gets: long enough for the visit and a week of follow-up, and no longer.
REASON_RETENTION_DEFAULT_DAYS = 30


class ConfirmationRequiredError(PermissionError):
    """The change exposes something on a public screen and was not explicitly confirmed."""


class RetentionOutOfRangeError(ValueError):
    """The retention window is outside the policy ceiling."""


# --------------------------------------------------------------------------------------
# The warning text: plain language, next to the rule it describes
# --------------------------------------------------------------------------------------

#: What each display mode actually puts on the screen, in the words a manager is shown. Written as
#: a description of the *screen*, not of the setting: "the board will show" rather than "enables
#: name display", because the person deciding has to picture the waiting room.
DISPLAY_MODE_WARNINGS: dict[DisplayMode, str] = {
    DisplayMode.NUMBER_ONLY: (
        "The board will show ticket numbers only, for example A014. No patient's name appears on "
        "the screen, and no name is sent to it."
    ),
    DisplayMode.NAME_LITE: (
        "The board will show each patient's first name and the initial of their surname beside "
        "their number, for example A014 Thandi M. Anyone in the waiting room, and anyone who can "
        "see the screen from outside it, will be able to read that."
    ),
    DisplayMode.FULL: (
        "The board will show each patient's full name beside their number, for example A014 "
        "Thandi Mokoena. Anyone in the waiting room, and anyone who can see the screen from "
        "outside it, will be able to read that. Only choose this if the screen cannot be seen "
        "from a public area."
    ),
}

#: The extra sentence a manager is shown when they also switch the comment on. It names the thing
#: that makes this different from the mode alone: a reason for a visit is health information.
COMMENT_WARNING = (
    "The board will also show the reason each patient gave for their visit, in their own words. "
    "That is health information about an identifiable person, shown in public. Each patient is "
    "still asked separately, at each visit, before their reason is shown."
)

#: And when the comment is switched on together with full names, which is the sharpest combination
#: the product can produce.
COMMENT_WITH_FULL_NAME_WARNING = (
    "Showing a reason beside a full name means anyone reading the board learns why a named person "
    "is at the clinic. Confirm that this screen cannot be seen from a public area."
)


#: What each board theme is called on the settings screen, and when a clinic would choose it (Issue 59).
#: Every theme passes the same contrast checks, so none of these is "the accessible one".
BOARD_THEME_CHOICES: dict[BoardTheme, tuple[str, str]] = {
    BoardTheme.DIM: (
        "Dim room",
        "Light numbers on a dark background. Easy on the eyes and no glare: the usual choice.",
    ),
    BoardTheme.BRIGHT: (
        "Bright room",
        "Dark numbers on a light background, for a screen in daylight where a dark one mirrors the windows.",
    ),
    BoardTheme.HIGH_CONTRAST: (
        "High contrast",
        "White and yellow on black with white borders, for patients with low vision.",
    ),
}


@dataclass(frozen=True, slots=True)
class DisplayWarning:
    """What a manager is told before a display change takes effect.

    ``requires_confirmation`` is the server's answer, not the screen's: a client that renders no
    warning still cannot make the change, because the confirmation is a field in the request.
    """

    #: The sentences to show, in order.
    lines: tuple[str, ...]
    #: Whether this change may not be made without ``confirm_public_display``.
    requires_confirmation: bool
    #: Whether it additionally needs ``confirm_comment_with_full_name``.
    requires_comment_confirmation: bool


def warning_for(*, display_mode: DisplayMode, show_comment: bool) -> DisplayWarning:
    """What the clinic would be agreeing to, and which confirmations it takes.

    A pure function of the two settings, so the screen, the API and the tests all read the same
    sentences and cannot drift apart.

    Args:
        display_mode: The mode being moved to.
        show_comment: Whether the visit's reason would be shown beside the ticket.

    Returns:
        The lines to show and the confirmations required.
    """
    reveals_name = display_mode in NAME_REVEALING_DISPLAY_MODES
    lines = [DISPLAY_MODE_WARNINGS[display_mode]]
    if show_comment:
        lines.append(COMMENT_WARNING)
    comment_with_full_name = show_comment and display_mode is DisplayMode.FULL
    if comment_with_full_name:
        lines.append(COMMENT_WITH_FULL_NAME_WARNING)
    return DisplayWarning(
        lines=tuple(lines),
        requires_confirmation=reveals_name or show_comment,
        requires_comment_confirmation=comment_with_full_name,
    )


def validate_retention_days(days: int) -> int:
    """Return ``days``, or raise if it is outside the policy ceiling.

    Raises:
        RetentionOutOfRangeError: If the window is shorter than a day or longer than
            :data:`REASON_RETENTION_CEILING_DAYS`. The message names the ceiling and where it comes
            from, because a manager asked for 180 days needs to know what to do about it.
    """
    if REASON_RETENTION_FLOOR_DAYS <= days <= REASON_RETENTION_CEILING_DAYS:
        return days
    raise RetentionOutOfRangeError(
        f"A visit's reason may be kept for {REASON_RETENTION_FLOOR_DAYS} to "
        f"{REASON_RETENTION_CEILING_DAYS} days. The ceiling is the platform's retention policy "
        "and a clinic cannot raise it."
    )


@dataclass(frozen=True, slots=True)
class DisplaySettingsChange:
    """One requested change to a clinic's display settings, and what it takes to make it."""

    display_mode: DisplayMode
    show_comment: bool
    board_language: BoardLanguage
    announce_audio: bool
    retention_days: int
    board_theme: BoardTheme = SITE_DEFAULT_BOARD_THEME
    announce_volume: int = SITE_DEFAULT_ANNOUNCE_VOLUME


def apply_display_settings(
    site: Site,
    change: DisplaySettingsChange,
    *,
    confirm_public_display: bool = False,
    confirm_comment_with_full_name: bool = False,
) -> tuple[Site, list[str]]:
    """Apply a display-settings change, refusing anything unconfirmed. The caller commits.

    Args:
        site: The clinic.
        change: What it should become.
        confirm_public_display: The manager's explicit agreement to a mode or comment setting that
            puts something about a patient on a public screen.
        confirm_comment_with_full_name: Their separate agreement to a reason beside a full name.

    Returns:
        ``(site, what changed)`` — the second a list of human-readable field descriptions for the
        audit row, so the trail says *what moved* rather than only *that something did*.

    Raises:
        ConfirmationRequiredError: If the change exposes something and was not confirmed.
        RetentionOutOfRangeError: If the retention window is outside the policy ceiling.
    """
    validate_retention_days(change.retention_days)
    warning = warning_for(
        display_mode=change.display_mode, show_comment=change.show_comment
    )
    # Only an *increase* in exposure needs confirming. Going back to number_only, or switching the
    # comment off, is always allowed: the safe direction never needs permission to travel in.
    already = warning_for(
        display_mode=site.display_mode_enum, show_comment=site.display_show_comment
    )
    newly_exposing = warning.requires_confirmation and not already.requires_confirmation
    if newly_exposing and not confirm_public_display:
        raise ConfirmationRequiredError(
            "This change puts patient information on a public screen. Confirm it explicitly: "
            + " ".join(warning.lines)
        )
    newly_commenting_on_a_name = (
        warning.requires_comment_confirmation
        and not already.requires_comment_confirmation
    )
    if newly_commenting_on_a_name and not confirm_comment_with_full_name:
        raise ConfirmationRequiredError(COMMENT_WITH_FULL_NAME_WARNING)

    changed: list[str] = []
    for field, before, after in (
        ("display_mode", site.display_mode, change.display_mode.value),
        ("display_show_comment", site.display_show_comment, change.show_comment),
        ("board_language", site.board_language, change.board_language.value),
        ("board_theme", site.board_theme, change.board_theme.value),
        ("announce_audio", site.announce_audio, change.announce_audio),
        ("announce_volume", site.announce_volume, change.announce_volume),
        ("reason_retention_days", site.reason_retention_days, change.retention_days),
    ):
        if before != after:
            changed.append(f"{field}: {before} -> {after}")
            setattr(site, field, after)
    return site, changed
