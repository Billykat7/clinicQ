"""Find the waiting-room screens this machine can see, and optionally reach one (Issue 237).

    python -m scripts.find_screens
    python -m scripts.find_screens --connect 192.168.3.106

The same code the dashboard's **Screens on this network** button runs
(:mod:`src.modules.display.discovery`), with nothing else in the way. That is the point of it: when
a manager presses the button and no screen answers, the cause is almost never ClinicQ — it is a
television asleep, a network that keeps its devices apart, or an operating system refusing this
process the local network — and each of those is quicker to see here, next to the machine, than
through a web page.

``--connect ADDRESS`` goes one step further and opens a Cast connection to one screen, reporting
whether it answered and whether it would show the board. It sends **no claim code**, so it pairs
nothing and changes nothing: it is a reachability check, not a setup step.

Runs against this machine's own network, whatever the deployment's settings say, because it is a
diagnostic: the ``SMART_TV_DISCOVERY_ENABLED`` switch governs what a *manager* may set off from the
dashboard, and an operator standing at the server has already decided to look.
"""

from __future__ import annotations

import argparse
import platform
import sys

from src.core.config import get_settings
from src.modules.display import casting, discovery


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.find_screens",
        description="Find Chromecast-capable screens on this machine's network.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=6.0,
        help="how long to listen for screens to answer (default: 6)",
    )
    parser.add_argument(
        "--connect",
        metavar="ADDRESS",
        default="",
        help="after searching, open a Cast connection to this address and report what it says",
    )
    return parser.parse_args(argv)


def _report_empty(note: str) -> None:
    """Print the scanner's own sentence, plus what an operator at the machine can do about it."""
    print("\nNo screen answered.\n")
    print(f"  {note}\n")
    if platform.system() == "Darwin":
        print(
            "  On this Mac, check it is not the local-network permission: run\n"
            "      dns-sd -B _googlecast._tcp local.\n"
            "  If that lists a screen and this did not, macOS is dropping this process's\n"
            "  answers. System Settings → Privacy & Security → Local Network, and allow the\n"
            "  program that runs the server (the Python binary, or the terminal it runs in).\n"
        )


def main(argv: list[str] | None = None) -> int:
    """Search, print what answered, and optionally try to reach one screen."""
    args = _parse(argv)
    settings = get_settings()
    # A diagnostic looks whatever the deployment's switch says; see the module docstring.
    looking = settings.model_copy(update={"smart_tv_discovery_enabled": True})

    print(f"Listening for {args.seconds:.0f}s on this machine's network…")
    outcome = discovery.scan_for_screens(settings=looking, seconds=args.seconds)

    if not outcome.screens:
        _report_empty(outcome.note)
    else:
        print(f"\n{outcome.found} screen(s) answered:\n")
        for screen in outcome.screens:
            print(f"  {screen.label_suggestion}")
            print(f"      address : {screen.address}:{screen.port}")
            print(f"      model   : {screen.model or '—'}")
            print(f"      made by : {screen.manufacturer or '—'}")
            print(f"      id      : {screen.uuid}")
            print()

    if not args.connect:
        return 0 if outcome.screens else 1

    print(f"Connecting to {args.connect}…")
    # No claim code: this proves the screen is reachable, it does not set one up.
    result = casting.send_board_to_screen(
        discovery.ScreenTarget(address=args.connect),
        claim_code="",
        board_url="",
        settings=looking,
    )
    print(f"\n  reached : {result.reached}")
    print(f"  showing : {result.showing}")
    print(f"  screen  : {result.screen_name or '—'}")
    print(f"  says    : {result.note}\n")
    if result.reached and not result.showing and not settings.cast_receiver_app_id:
        print(
            "  The screen is reachable and ClinicQ has no receiver application registered\n"
            "  yet, which is the expected answer until CAST_RECEIVER_APP_ID is set.\n"
            "  See docs/OPS/SMART_TV_SETUP.md.\n"
        )
    return 0 if result.reached else 1


if __name__ == "__main__":  # pragma: no cover - a console entry point
    sys.exit(main())
