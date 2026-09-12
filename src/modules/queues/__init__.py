"""Queues: a clinic's named lines, calling the next patient, and the tickets in each queue.

Read the files in this order:

* :mod:`.rbac_manifest` — the module's authorization footprint, declared by Issue 18 ahead of the
  module itself, so the difference between a receptionist and a nurse is the **tier** of one grant
  rather than a different verb;
* :mod:`.schemas` — what a clinic manager may set, and what a channel menu is told;
* :mod:`.service` — the rules and every query, including the two that outlive Issue 25:
  deactivation keeps a queue's history, and a walk-in-only queue is refused **here**, on the
  server, rather than by a client declining to show a button;
* :mod:`.router` — eight routes, all under ``/sites/{site_id}/queues`` and all behind the site
  guard, because a queue does not exist outside a clinic.

Queues and rooms landed with Issue 25. Staff-to-room assignment comes with Issue 28, tickets with
Issue 39, and call-next with Issue 42 — all in this module, because they are the same engine.
"""
