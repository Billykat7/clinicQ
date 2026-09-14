"""The queue engine (M6): tickets, their numbers, and the read model built on them.

* :mod:`.sequence` — ticket numbers allocated inside the database under a unique constraint, the
  Johannesburg service day, and the six-character reference codes a person can read aloud
  (Issue 39).
* :mod:`.tickets` — the board's query and a patient's own tickets, each on its index (Issue 39).
* :mod:`.snapshot` — each queue's last known length, cached in Redis with the database behind it, so
  discovery reads twenty clinics' queues in one round trip and a cold cache is slower, never wrong
  (Issue 36).

Joining (Issue 40) and the lifecycle (Issue 41) land here too: they are the writers whose changes
:func:`.snapshot.on_queue_changed` makes visible. Queue *configuration* (names, rooms, remote joins)
stays in :mod:`src.modules.queues`.
"""
