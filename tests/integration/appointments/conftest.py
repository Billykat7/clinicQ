"""The appointments suite reuses the queue engine's HTTP fixture (M6): two open, listed clinics.

Both clinics are open around the clock (every weekday 00:00 to 00:00), so a slot is never refused by
the hour the suite happens to run at, and a slot crossing midnight is inside the clinic's hours.
Clinic A's Triage takes remote joins; ``desk.a`` is its receptionist and ``manager.a`` its manager.
"""

from tests.integration.queue.conftest import (
    desk,  # noqa: F401 (a fixture, used by name)
)
