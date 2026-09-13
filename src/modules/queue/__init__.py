"""The queue engine (M6), starting with its read model: the queue snapshot (Issue 36).

* :mod:`.snapshot` — each queue's last known length, cached in Redis with the database behind it, so
  discovery reads twenty clinics' queues in one round trip and a cold cache is slower, never wrong.

Tickets and their sequence numbers (Issue 39), joining (Issue 40) and the lifecycle (Issue 41) land
here too: they are the writers whose changes :func:`.snapshot.on_queue_changed` makes visible. Queue
*configuration* (names, rooms, remote joins) stays in :mod:`src.modules.queues`.
"""
