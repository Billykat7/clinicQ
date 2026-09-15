"""Fixtures for the notification integration tests.

``desk`` is the queue engine's fixture (two clinics, their queues and receptionists), reused so a
patient notification is exercised against the real queue moves that cause it (Issue 63).
"""

from tests.integration.queue.conftest import desk

__all__ = ["desk"]
