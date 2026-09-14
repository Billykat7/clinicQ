"""Visits: what happens to a patient inside the consulting room (Issues 48 and 53).

A visit's journey through the queues (its tickets, transfers and timings) belongs to the queue
engine (:mod:`src.modules.queue.transfer`). This module owns the part that is **clinical**: the
short, private visit notes a nurse or doctor writes in the room. That is health information, so it
has its own resource tree, reachable only by the clinicians who write it, and never by the front
desk, the waiting-room board or the patient's own page.
"""
