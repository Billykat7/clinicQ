"""The network scanner's own judgement, with no network involved (Issue 237).

:mod:`src.modules.display.discovery` reads a hostile source — whatever answers a multicast question
on the network a server happens to be plugged into — and hands the result to a manager's page. These
tests are about the parts of that which must not depend on a television being in the room: what it
does with a nonsense answer, and which sentence it gives a manager when the list is empty, since an
empty list has four different causes and only the sentence tells them apart.

The library that speaks to the network is replaced throughout. What is *not* replaced is the module's
own reasoning, which is the only thing here worth asserting.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from src.core.config import Settings
from src.modules.display import discovery


def _settings(**overrides: object) -> Settings:
    """Isolated settings with the feature on, so a developer's ``.env`` cannot change the answer."""
    base: dict[str, object] = {
        "_env_file": None,
        "smart_tv_discovery_enabled": True,
        "jwt_secret": "screen-scanner-test-secret-min-32-characters",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _Info:
    """A stand-in for PyChromecast's ``CastInfo``, built with whatever a test wants to say."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _fake_pychromecast(monkeypatch: pytest.MonkeyPatch, infos: list[Any]) -> list[str]:
    """Install a fake ``pychromecast`` whose search returns ``infos``; returns a call log."""
    calls: list[str] = []
    module = types.ModuleType("pychromecast")
    sub = types.ModuleType("pychromecast.discovery")

    def _discover(timeout: float = 5.0, **_: object) -> tuple[list[Any], object]:
        calls.append(f"discover:{timeout}")
        return infos, object()

    def _stop(browser: object) -> None:
        calls.append("stop")

    sub.discover_chromecasts = _discover  # type: ignore[attr-defined]
    sub.stop_discovery = _stop  # type: ignore[attr-defined]
    module.discovery = sub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pychromecast", module)
    monkeypatch.setitem(sys.modules, "pychromecast.discovery", sub)
    return calls


# --- The four empty answers ------------------------------------------------------


def test_a_deployment_with_the_feature_off_never_touches_the_network() -> None:
    """Off means off: no import, no socket, and a sentence saying which of the four this is."""
    outcome = discovery.scan_for_screens(
        settings=_settings(smart_tv_discovery_enabled=False)
    )

    assert outcome.screens == () and outcome.found == 0
    assert outcome.searched is False
    assert outcome.note == discovery.OFF_NOTE


def test_a_missing_package_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator who turned this on without installing the extra is told exactly that.

    The import is deliberately inside the call, so this is the state a running server can be in: the
    setting says search, the package is not there, and the page must say something a person can act
    on rather than returning a 500.
    """
    monkeypatch.setitem(sys.modules, "pychromecast", None)

    outcome = discovery.scan_for_screens(settings=_settings())

    assert outcome.searched is False
    assert outcome.note == discovery.MISSING_PACKAGE_NOTE


def test_a_network_that_refuses_us_reads_as_an_empty_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interface that will not carry the question is an outcome, not a crash."""
    module = types.ModuleType("pychromecast")
    sub = types.ModuleType("pychromecast.discovery")

    def _refuse(**_: object) -> tuple[list[Any], object]:
        raise OSError("no route to host")

    sub.discover_chromecasts = _refuse  # type: ignore[attr-defined]
    sub.stop_discovery = lambda browser: None  # type: ignore[attr-defined]
    module.discovery = sub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pychromecast", module)

    outcome = discovery.scan_for_screens(settings=_settings())

    assert outcome.screens == ()
    assert outcome.searched is True
    assert discovery.NOTHING_FOUND_NOTE in outcome.note


def test_on_macos_the_empty_answer_also_names_the_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS drops these answers silently until a person allows the program, which looks like nothing.

    Worth its own sentence because the failure is indistinguishable from an empty network: the same
    query answers for the system's own ``dns-sd`` and returns nothing whatever to a process without
    the grant, with no error to log.
    """
    _fake_pychromecast(monkeypatch, [])
    monkeypatch.setattr(discovery.platform, "system", lambda: "Darwin")

    outcome = discovery.scan_for_screens(settings=_settings())

    assert discovery.MACOS_PERMISSION_NOTE.strip() in outcome.note


def test_on_linux_the_empty_answer_does_not_mention_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clinic's own box runs Linux, where there is no such gate and the hint would mislead."""
    _fake_pychromecast(monkeypatch, [])
    monkeypatch.setattr(discovery.platform, "system", lambda: "Linux")

    outcome = discovery.scan_for_screens(settings=_settings())

    assert outcome.note == discovery.NOTHING_FOUND_NOTE


# --- What comes back off the network ---------------------------------------------


def test_screens_are_reported_with_a_name_to_offer_and_the_search_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case, and the teardown that goes with it: a search leaves no listener behind."""
    calls = _fake_pychromecast(
        monkeypatch,
        [
            _Info(
                host="192.168.3.106",
                port=8009,
                uuid="ec9b8350-bd0d-f7e8-9faa-ace4909c455f",
                friendly_name="Smart TV Pro",
                model_name="Smart TV Pro",
                manufacturer="Generic",
            )
        ],
    )

    outcome = discovery.scan_for_screens(settings=_settings(), seconds=2.0)

    assert outcome.found == 1
    only = outcome.screens[0]
    assert only.address == "192.168.3.106" and only.port == 8009
    assert only.label_suggestion == "Smart TV Pro"
    assert calls == ["discover:2.0", "stop"]


def test_a_nonsense_answer_costs_one_row_and_not_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything on the network may answer. A responder with no address, no id or a junk port is skipped.

    The alternative — trusting the shape of whatever replied — turns one hostile device on a clinic's
    network into a 500 on the manager's settings page.
    """
    _fake_pychromecast(
        monkeypatch,
        [
            _Info(host="", uuid="no-address", friendly_name="Nameless"),
            _Info(host="192.168.3.2", uuid="", friendly_name="No id"),
            _Info(
                host="192.168.3.3",
                port="not-a-port",
                uuid="11111111-1111-1111-1111-111111111111",
                friendly_name="Bad port",
                model_name="TV",
            ),
        ],
    )

    outcome = discovery.scan_for_screens(settings=_settings())

    assert outcome.found == 1
    assert outcome.screens[0].address == "192.168.3.3"
    # Unreadable port falls back to the one every Chromecast listens on rather than being dropped.
    assert outcome.screens[0].port == discovery.CAST_PORT


def test_a_screen_cannot_write_control_characters_onto_a_managers_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name is a name, not a payload: control characters go, and length is capped."""
    _fake_pychromecast(
        monkeypatch,
        [
            _Info(
                host="192.168.3.4",
                port=8009,
                uuid="22222222-2222-2222-2222-222222222222",
                friendly_name="Lobby\r\nTV\x00" + "x" * 400,
                model_name="TV",
            )
        ],
    )

    outcome = discovery.scan_for_screens(settings=_settings())

    name = outcome.screens[0].name
    assert "\r" not in name and "\n" not in name and "\x00" not in name
    assert name.startswith("Lobby  TV")
    assert len(name) <= discovery.MAX_FIELD


def test_one_screen_answering_twice_is_still_one_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that answers on two interfaces is one television in one room."""
    twice = _Info(
        host="192.168.3.106",
        port=8009,
        uuid="ec9b8350-bd0d-f7e8-9faa-ace4909c455f",
        friendly_name="Smart TV Pro",
        model_name="Smart TV Pro",
    )
    _fake_pychromecast(monkeypatch, [twice, twice])

    assert discovery.scan_for_screens(settings=_settings()).found == 1


def test_a_flood_of_responders_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clinic has a handful of screens; anything past the cap is noise, not a waiting room."""
    _fake_pychromecast(
        monkeypatch,
        [
            _Info(
                host=f"192.168.3.{n}",
                port=8009,
                uuid=f"{n:08d}-0000-0000-0000-000000000000",
                friendly_name=f"Screen {n}",
                model_name="TV",
            )
            for n in range(1, 200)
        ],
    )

    assert (
        discovery.scan_for_screens(settings=_settings()).found == discovery.MAX_SCREENS
    )
