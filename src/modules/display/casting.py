"""Sending the waiting-room board to a screen the server found on its network (Issue 237).

:mod:`src.modules.display.discovery` answers *which screens are there*. This module answers *put the
board on that one* — the *connection* half of "discover and connect".

**What a Chromecast will and will not do.** It is not a browser you can point anywhere. It runs a
**receiver application**, and an application is a URL that has been registered with Google at
``cast.google.com/publish`` under an id; a sender may launch a registered id and nothing else. So a
deployment that wants the board on a Chromecast registers ClinicQ's receiver page once and puts the
id in :data:`~src.core.config.Settings.cast_receiver_app_id` (``docs/OPS/SMART_TV_SETUP.md``).
Without it every other part of this feature still works — the screen is found, reached and reported
on — and this module says plainly that the last step is not configured rather than failing obscurely.

**How the screen becomes this clinic's screen.** The registered URL is fixed, so the clinic cannot be
named in it. Instead the dashboard holds a row open for the screen
(:func:`~src.modules.display.devices.start_claimable_device`) and this module hands the screen its
one-time claim code over the Cast connection, on the clinic's own network. The receiver page spends
the code at ``/display/claim``, is given its device secret, and opens the board. Nobody types
anything, and the code is spent the moment it is used.

The claim code is sent as an application message rather than in the launch, because the launch
carries no payload of ours: the receiver is already loading its own registered page and we speak to
it once it answers on :data:`CLINICQ_NAMESPACE`.

Every call is blocking network work with a bounded timeout, so a route runs it off the event loop.
Nothing here raises for an unreachable or uncooperative screen: a :class:`CastOutcome` always comes
back, carrying a sentence a clinic manager can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from src.core.config import Settings, get_settings
from src.modules.display.discovery import CAST_PORT, ScreenTarget

logger = logging.getLogger(__name__)

#: The application channel ClinicQ's receiver listens on. Namespaces are ``urn:x-cast:`` plus a name
#: the application owns; this one is ours and the receiver page declares the same string.
CLINICQ_NAMESPACE: Final = "urn:x-cast:za.co.clinicq.board"
#: How long to wait for a screen to accept a connection and answer, in seconds. A TV waking from
#: standby is slow; a TV that is off never answers at all, and the manager should be told quickly.
CONNECT_TIMEOUT_SECONDS: Final = 12.0
#: How long to wait for the receiver application to report itself running after it is launched.
LAUNCH_TIMEOUT_SECONDS: Final = 20.0

#: Said when a deployment has not registered a receiver application.
NO_APP_ID_NOTE: Final = (
    "This server can reach the screen, but no Cast receiver application is registered for ClinicQ, "
    "so the screen has no ClinicQ page it is allowed to open. An operator sets CAST_RECEIVER_APP_ID "
    "(see docs/OPS/SMART_TV_SETUP.md). Until then, pair the screen with the code it shows."
)
#: Said when the optional package is absent.
MISSING_PACKAGE_NOTE: Final = (
    "This server cannot talk to Cast screens yet: the optional PyChromecast package is not "
    "installed."
)
#: Said when the screen did not answer at all.
UNREACHABLE_NOTE: Final = (
    "The screen did not answer. Check that it is on (not only standby) and still on this server's "
    "network, then search again — a screen that has been asleep often answers on the second try."
)
#: Said when the screen answered but would not start the application.
LAUNCH_FAILED_NOTE: Final = (
    "The screen answered but would not open ClinicQ. Check that the receiver application id is the "
    "published one and that this screen is allowed to run it."
)


@dataclass(frozen=True, slots=True)
class CastOutcome:
    """What happened when the server tried to put the board on one screen.

    ``reached`` and ``showing`` are deliberately separate: reaching a screen proves the address, the
    network and the TV, and is worth reporting even when the last step could not be taken because no
    receiver application is registered. A caller that collapses the two into one boolean would tell
    a manager "it failed" about a screen that is, in fact, sitting there waiting for one setting.
    """

    #: Whether the server opened a connection to the screen and it answered.
    reached: bool
    #: Whether the screen was handed the board and acknowledged the launch.
    showing: bool
    #: A sentence for the manager, whatever the outcome.
    note: str
    #: What the screen calls itself, as it reported on connection.
    screen_name: str = ""


def _clinicq_controller(namespace: str) -> Any:
    """Build the one controller ClinicQ speaks through, subclassed here so the import stays optional.

    PyChromecast's ``BaseController`` is the class every application channel extends. Ours adds
    nothing but its namespace and the id it belongs to: we send one message and do not care what the
    receiver says back, because the receiver's real answer is the board appearing on the wall.
    """
    from pychromecast.controllers import BaseController

    class _ClinicQController(BaseController):
        def __init__(self, app_id: str) -> None:
            super().__init__(namespace, supporting_app_id=app_id, app_must_match=True)

    return _ClinicQController


def send_board_to_screen(
    target: ScreenTarget,
    *,
    claim_code: str,
    board_url: str,
    settings: Settings | None = None,
) -> CastOutcome:
    """Connect to ``target`` and ask it to show the board, claiming its row with ``claim_code``.

    Blocking, bounded by :data:`CONNECT_TIMEOUT_SECONDS` and :data:`LAUNCH_TIMEOUT_SECONDS`, and
    always disconnects: a clinic's server must not accumulate open sockets to televisions.

    ``board_url`` is the absolute address the receiver opens after it has spent the claim code — the
    board belongs to this deployment, and a screen on the clinic's network may reach the server by a
    different name than a manager's laptop does, so the caller resolves it rather than this module
    guessing.
    """
    cfg = settings or get_settings()
    try:
        import pychromecast
    except ImportError:
        logger.warning("A cast was attempted but PyChromecast is not installed.")
        return CastOutcome(reached=False, showing=False, note=MISSING_PACKAGE_NOTE)

    # The library's own failures (a screen that never answers, a connection that drops mid-launch)
    # are ordinary outcomes here, not faults: caught by name so a television being off does not put
    # a stack trace in a clinic's log every time a manager presses the button.
    cast_errors: tuple[type[BaseException], ...] = (
        pychromecast.error.PyChromecastError,
        OSError,
        RuntimeError,
        ValueError,
    )
    app_id = (cfg.cast_receiver_app_id or "").strip()
    cast = None
    try:
        cast = pychromecast.get_chromecast_from_host(
            (
                target.address,
                int(target.port or CAST_PORT),
                _uuid_or_random(target.uuid),
                None,
                target.name or None,
            ),
            tries=1,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        cast.wait(timeout=CONNECT_TIMEOUT_SECONDS)
        name = str(cast.name or target.name or "")
        if not app_id:
            # Reached, and that is genuinely worth saying: the address and the network are proven,
            # and only the one-off registration is missing.
            return CastOutcome(
                reached=True, showing=False, note=NO_APP_ID_NOTE, screen_name=name
            )
        controller = _clinicq_controller(CLINICQ_NAMESPACE)(app_id)
        # The handler lives on the socket client: the connection owns the channel, not the device.
        cast.socket_client.register_handler(controller)
        cast.start_app(app_id)
        if not _app_running(cast, app_id):
            return CastOutcome(
                reached=True, showing=False, note=LAUNCH_FAILED_NOTE, screen_name=name
            )
        controller.send_message(
            {"type": "clinicq.board", "claim": claim_code, "board_url": board_url}
        )
        return CastOutcome(
            reached=True,
            showing=True,
            note=f"{name or 'The screen'} is opening the waiting-room board.".strip(),
            screen_name=name,
        )
    except cast_errors as exc:
        logger.info("Could not cast to %s: %s", target.address, exc)
        return CastOutcome(reached=False, showing=False, note=UNREACHABLE_NOTE)
    except Exception:  # pragma: no cover - third-party failure modes are not enumerable
        logger.warning(
            "Casting to %s failed unexpectedly", target.address, exc_info=True
        )
        return CastOutcome(reached=False, showing=False, note=UNREACHABLE_NOTE)
    finally:
        if cast is not None:
            try:
                cast.disconnect(timeout=CONNECT_TIMEOUT_SECONDS)
            except Exception:  # pragma: no cover - best effort teardown
                logger.debug("Disconnecting from the screen failed", exc_info=True)


def _uuid_or_random(raw: str) -> UUID:
    """``raw`` as a UUID, or a fresh one when the screen gave none we can read.

    PyChromecast keys a connection by uuid and insists on the type; the value only has to be stable
    for the life of this connection, which a fresh one is.
    """
    from uuid import uuid4

    try:
        return UUID(raw)
    except ValueError, AttributeError, TypeError:
        return uuid4()


def _app_running(cast: Any, app_id: str) -> bool:
    """Whether ``cast`` reports ``app_id`` as its running application, waited for briefly.

    ``start_app`` returns as soon as the request is sent; the screen decides afterwards. Polling its
    reported status is how a refused launch (a wrong id, an application this device may not run)
    becomes a sentence rather than a cast that silently never appears.
    """
    import time

    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if getattr(cast, "app_id", None) == app_id:
            return True
        time.sleep(0.5)
    return False
