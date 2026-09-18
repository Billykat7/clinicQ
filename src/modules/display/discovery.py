"""Finding the Chromecast-capable screens on the server's own network (Issue 237).

The waiting-room board's usual home is a small box on the TV's HDMI port
(``docs/OPS/KIOSK_SETUP.md``): it is provisioned once, shows a code, and a manager types that code
into the dashboard. Nothing about it depends on the TV, which is why it works on every TV.

This module is the other way in, for a clinic whose TV **is** the computer. A Chromecast — built
into most Android TV and Google TV sets — announces itself on the local network over mDNS as
``_googlecast._tcp``. So a manager standing at the dashboard can be shown the screens in the
building and pick one, instead of reading a code off it.

**It searches the *server's* network, not the manager's.** mDNS is a multicast question shouted at
the local link; the answers come back only to a process on that link. The browser cannot ask (a web
page has no multicast), so the server asks, and the server is only in the right building when it
runs there — a clinic's own box, or a laptop during a demo. A cloud instance shares no network with
the clinic and finds nothing, honestly and every time: :data:`~src.core.config.Settings.smart_tv_discovery_enabled`
is therefore off until a deployment says otherwise. :class:`ScanOutcome` carries a sentence saying
which of those is happening, because "no screens found" on its own is the one answer a manager
cannot act on.

**Finding is not the same as showing.** What a Chromecast will load is decided by Google's registry,
not by us: it runs a *receiver application*, named by an id registered at ``cast.google.com/publish``,
and refuses an unregistered page. Discovery here is therefore useful on its own — it proves the
screen exists, is awake and is reachable — and :mod:`src.modules.display.casting` is where the
board is actually sent, when a deployment has registered a receiver.

PyChromecast (and the ``zeroconf`` it brings) is an **optional** dependency, imported only when a
search runs: a deployment that never turns this on never installs it, and one that turns it on
without installing it is told exactly that rather than failing with an ``ImportError``.
"""

from __future__ import annotations

import logging
import platform
import re
from dataclasses import dataclass, field
from typing import Final

from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: The mDNS service a Chromecast (and every Android TV with Chromecast built in) answers to.
CAST_SERVICE: Final = "_googlecast._tcp.local."
#: The port a Chromecast listens on for the protocol that launches an application.
CAST_PORT: Final = 8009
#: What a screen is called when it announces no friendly name of its own.
UNNAMED: Final = "Unnamed screen"
#: The longest name, model or id we keep off the network, so a hostile responder cannot flood a page.
MAX_FIELD: Final = 120
#: How many screens one search reports. A clinic has a handful; a flood is not a clinic.
MAX_SCREENS: Final = 50

#: Said when the deployment has not turned searching on.
OFF_NOTE: Final = (
    "Searching the network for screens is switched off for this deployment. A waiting-room board "
    "is paired with the code on its screen instead."
)
#: Said when the optional package is missing.
MISSING_PACKAGE_NOTE: Final = (
    "This server cannot search the network yet: the optional PyChromecast package is not "
    "installed. Install it and restart, or pair the board with the code on its screen."
)
#: Said when the search ran and nothing answered, whatever the platform.
NOTHING_FOUND_NOTE: Final = (
    "No Chromecast-capable screen answered on this server's network. Check that the screen is on "
    "(not only standby), that it is on the same network as this server, and that the network does "
    "not keep its devices apart."
)
#: Added on macOS, where a process is denied the local network until a person allows it, and is
#: given no error when it is: an unallowed process simply never hears an answer, which looks
#: exactly like an empty network. Verified on macOS 26: the same query answers for the system's own
#: ``dns-sd`` and returns nothing at all for a Python process without the grant.
MACOS_PERMISSION_NOTE: Final = (
    " On macOS, also check System Settings → Privacy & Security → Local Network and allow the "
    "program running this server; without that macOS drops these answers silently."
)


@dataclass(frozen=True, slots=True)
class DiscoveredScreen:
    """One Chromecast-capable screen that answered, as the dashboard shows it.

    ``uuid`` is the screen's own identifier, stable across reboots and address changes, so the same
    TV picked twice is the same row. ``address`` and ``port`` are where the server reached it, which
    is what :mod:`src.modules.display.casting` connects back to.
    """

    uuid: str
    name: str
    model: str
    address: str
    port: int = CAST_PORT
    #: Who made it, when the screen says ("Google Inc."). Shown beside the name so two identical
    #: model names in one building are still tellable apart.
    manufacturer: str = ""

    @property
    def label_suggestion(self) -> str:
        """A name to offer for this screen, which the manager can overwrite."""
        return self.name or self.model or UNNAMED


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    """What a search found, and — when that is nothing — a sentence saying why.

    ``note`` is written for a clinic manager, not a developer: every empty result has a cause they
    can do something about (the TV is asleep, the server is in another building, an operator has
    not turned the feature on), and a bare "none found" hides which.
    """

    screens: tuple[DiscoveredScreen, ...] = ()
    note: str = ""
    #: Whether the search actually ran. False when switched off or the package is missing, so the
    #: page can offer the code instead of inviting the manager to try again.
    searched: bool = True

    @property
    def found(self) -> int:
        """How many screens answered."""
        return len(self.screens)


def _clean(value: object) -> str:
    """One field off the network, made safe to store and show: text, trimmed, length-capped."""
    if not isinstance(value, (str, bytes)):
        return ""
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    # A screen names itself; it does not get to inject control characters into a manager's page.
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()
    return text[:MAX_FIELD]


def _screen_from_cast_info(info: object) -> DiscoveredScreen | None:
    """Turn one PyChromecast ``CastInfo`` into a :class:`DiscoveredScreen`, or ``None`` if unusable.

    Defensive about the attributes because they come from a third-party library reading a hostile
    network: a responder that answers with nonsense should cost us one skipped row, not a 500 on a
    manager's page.
    """
    host = _clean(getattr(info, "host", ""))
    uuid = _clean(str(getattr(info, "uuid", "") or ""))
    if not host or not uuid:
        return None
    try:
        port = int(getattr(info, "port", CAST_PORT) or CAST_PORT)
    except TypeError, ValueError:
        port = CAST_PORT
    return DiscoveredScreen(
        uuid=uuid,
        name=_clean(getattr(info, "friendly_name", "")),
        model=_clean(getattr(info, "model_name", "")),
        address=host,
        port=port,
        manufacturer=_clean(getattr(info, "manufacturer", "")),
    )


def _empty_note() -> str:
    """The sentence shown when a search ran and nothing answered."""
    note = NOTHING_FOUND_NOTE
    if platform.system() == "Darwin":
        note += MACOS_PERMISSION_NOTE
    return note


def scan_for_screens(
    *,
    settings: Settings | None = None,
    seconds: float | None = None,
) -> ScanOutcome:
    """Search the server's network for Chromecast-capable screens for ``seconds``.

    Blocking: it listens on the network for as long as it is given, so a route calls it off the
    event loop (``asyncio.to_thread``). Never raises for a network that answers nothing, a missing
    package or a platform that refuses to ask — each is an outcome with a sentence attached.
    """
    cfg = settings or get_settings()
    if not cfg.smart_tv_discovery_enabled:
        return ScanOutcome(note=OFF_NOTE, searched=False)
    try:
        import pychromecast
    except ImportError:
        logger.warning("Smart TV discovery is on but PyChromecast is not installed.")
        return ScanOutcome(note=MISSING_PACKAGE_NOTE, searched=False)

    timeout = float(seconds or cfg.smart_tv_discovery_seconds)
    browser = None
    try:
        # discover_chromecasts owns its own zeroconf instance and stops listening before it
        # returns, so a search leaves no socket behind on a server that runs for months.
        infos, browser = pychromecast.discovery.discover_chromecasts(timeout=timeout)
    except OSError as exc:
        # The local network refused us outright — no route, no permission, no interface.
        logger.warning("Smart TV discovery could not use the network: %s", exc)
        return ScanOutcome(note=_empty_note())
    finally:
        if browser is not None:
            try:
                pychromecast.discovery.stop_discovery(browser)
            except Exception:  # pragma: no cover - best effort teardown
                logger.debug("Stopping cast discovery failed", exc_info=True)

    screens: list[DiscoveredScreen] = []
    seen: set[str] = set()
    for info in infos or ():
        screen = _screen_from_cast_info(info)
        if screen is None or screen.uuid in seen:
            continue
        seen.add(screen.uuid)
        screens.append(screen)
        if len(screens) >= MAX_SCREENS:
            break
    screens.sort(key=lambda s: (s.label_suggestion.lower(), s.address))
    if not screens:
        return ScanOutcome(note=_empty_note())
    return ScanOutcome(screens=tuple(screens))


@dataclass(frozen=True, slots=True)
class ScreenTarget:
    """Where to reach a screen the manager picked, as the dashboard sends it back.

    A search's result is not kept: a manager picks a screen from a list they are looking at, and the
    address they picked comes back with the request. Nothing here is a credential — the screen is
    reached over the clinic's own network, and what makes the board *theirs* is the one-time code
    the connection carries (:func:`src.modules.display.devices.start_claimable_device`).
    """

    address: str
    port: int = CAST_PORT
    name: str = ""
    uuid: str = ""
    extras: dict[str, str] = field(default_factory=dict)
