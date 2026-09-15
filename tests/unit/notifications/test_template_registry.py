"""The template registry's variable contract, checked at registration, never at send time (Issue 66).

Pure logic and the shipped locale files, so unit tests:

* **a missing (or misspelt) variable fails at registration**, and so does every other way a version can break
  the contract: a blank reaching inside a value, no ticket number, a web push saying more than the number and
  clinic, a title where the channel has none, an SMS longer than one segment;
* **every event has a template in every channel**, in every language that has a locale file;
* **a version's words never change after the fact**: the locale files match their lock, so changing words
  without raising the version fails here;
* **the same version and context always render the same message.**
"""

from __future__ import annotations

import json

import pytest

from scripts.lock_notification_templates import LOCK, current_lock
from src.commons.enums import (
    DEFAULT_NOTIFICATION_LANGUAGE,
    PATIENT_EVENT_TEMPLATE,
    BoardLanguage,
    NotificationChannel,
    NotificationTemplate,
)
from src.modules.notifications import template_registry as registry
from src.modules.notifications.template_registry import TemplateError, TemplateText


def _text(
    body: str,
    *,
    template: NotificationTemplate = NotificationTemplate.TICKET_NEXT,
    channel: NotificationChannel = NotificationChannel.SMS,
    subject: str | None = None,
) -> TemplateText:
    return TemplateText(
        template=template,
        channel=channel,
        language=BoardLanguage.ENGLISH,
        version=1,
        body=body,
        subject=subject,
    )


@pytest.mark.parametrize(
    ("body", "subject", "channel", "reason"),
    [
        (
            "Ticket {number} at {clnic}.",
            None,
            NotificationChannel.SMS,
            "{clnic} cannot be used",
        ),
        (
            "Ticket {number}, wait {wait}.",
            None,
            NotificationChannel.SMS,
            "{wait} cannot be used",
        ),
        (
            "Ticket {number.__class__}.",
            None,
            NotificationChannel.SMS,
            "is not a plain blank",
        ),
        ("Ticket {number:>40}.", None, NotificationChannel.SMS, "is not a plain blank"),
        ("Ticket {number!r}.", None, NotificationChannel.SMS, "is not a plain blank"),
        ("Ticket {}.", None, NotificationChannel.SMS, "is not a plain blank"),
        (
            "Ticket {number at {clinic}.",
            None,
            NotificationChannel.SMS,
            "braces do not match",
        ),
        (
            "You are next at {clinic}.",
            None,
            NotificationChannel.SMS,
            "must say the ticket number",
        ),
        (
            "Ticket {number} in {queue}.",
            "Next",
            NotificationChannel.WEB_PUSH,
            "{queue} cannot be used in a web push",
        ),
        (
            "Ticket {number}.",
            "Next in {room}",
            NotificationChannel.WEB_PUSH,
            "{room} cannot be used in a web push",
        ),
        ("Ticket {number}.", None, NotificationChannel.WEB_PUSH, "needs a title"),
        ("Ticket {number}.", "Next", NotificationChannel.SMS, "has no title"),
        (
            "{app}: ticket {number} at {clinic}, you are next. Please be ready at {where}. "
            "Remember to bring your clinic card and your ID book.",
            None,
            NotificationChannel.SMS,
            "more than one segment",
        ),
    ],
)
def test_a_version_that_breaks_the_contract_fails_at_registration(
    body: str, subject: str | None, channel: NotificationChannel, reason: str
) -> None:
    """How to verify, step 1: registration fails, with a sentence saying why; no send is involved."""
    with pytest.raises(
        TemplateError, match=reason.replace("{", r"\{").replace("}", r"\}")
    ):
        registry.validate(_text(body, channel=channel, subject=subject))


def test_a_version_within_the_contract_registers_and_reports_its_worst_case_size() -> (
    None
):
    worst = registry.validate(
        _text("{app}: ticket {number}, please come in now to {where} at {clinic}.")
    )
    assert worst is not None and worst.segments == 1


def test_every_event_has_a_template_in_every_channel_in_every_written_language() -> (
    None
):
    """Loading the locale files is the registration: it raises on any version that breaks the contract."""
    registry.builtin.cache_clear()
    assert registry.languages()[0] is DEFAULT_NOTIFICATION_LANGUAGE
    for language in registry.languages():
        texts = registry.builtin(language)
        missing = [
            f"{template.value}/{channel.value}"
            for template in PATIENT_EVENT_TEMPLATE.values()
            for channel in registry.PATIENT_CHANNELS
            if (template, channel) not in texts
        ]
        assert missing == [], f"{language.value} has no words for {missing}"


def test_a_locale_file_that_breaks_the_contract_does_not_load(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "en"  # type: ignore[operator]
    folder.mkdir()
    (folder / "notifications.toml").write_text(
        '[ticket_next.sms]\nversion = 1\nbody = "{app}: ticket {number} at {clinci}."\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "LOCALES_DIR", tmp_path)
    registry.builtin.cache_clear()
    try:
        with pytest.raises(
            TemplateError, match=r"\[ticket_next\.sms\] \(en\).*\{clinci\}"
        ):
            registry.builtin(BoardLanguage.ENGLISH)
    finally:
        registry.builtin.cache_clear()


def test_changed_words_need_a_new_version_number() -> None:
    """The lock is what a sent message's version promises: words and version move together."""
    registry.builtin.cache_clear()
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    now = current_lock()
    reworded = [
        key
        for key, entry in now.items()
        if key in locked
        and entry["sha256"] != locked[key]["sha256"]
        and entry["version"] == locked[key]["version"]
    ]
    assert reworded == [], (
        f"these templates' words changed but their version did not: {reworded}. Raise `version` in the "
        "locale file, then run `python -m scripts.lock_notification_templates`."
    )
    assert now == locked, (
        "src/locales/notifications.lock.json is out of date: run `python -m scripts.lock_notification_templates`."
    )


def test_the_same_version_and_context_always_render_the_same_words() -> None:
    text = registry.builtin_text(
        NotificationTemplate.TICKET_RECALLED, NotificationChannel.SMS
    )
    context = dict(registry.SAMPLE)
    first = registry.render(
        text.template, text.channel, text.body, text.subject, context
    )
    again = registry.render(
        text.template, text.channel, text.body, text.subject, dict(context)
    )
    assert first == again
    assert (
        ": ticket A043 at Zola Community Clinic: we called you once more." in first.text
    )
