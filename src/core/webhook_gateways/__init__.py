"""Inbound payment-gateway webhooks: verify the signature, apply the event exactly once.

Two things make an unauthenticated endpoint safe to expose, and both live here rather than in the
route:

* **Signature verification.** A gateway cannot present a bearer token, so trust comes entirely
  from the signature over the *raw request bytes* — checked before the JSON is parsed, so a
  tampered payload never reaches your handlers.
* **Idempotency.** A gateway delivers each event at least once: retries, a slow ``200``, a manual
  replay from its dashboard. The event id is claimed in the same transaction as its effect, so an
  event is marked processed if and only if its effect committed.

What an event *means* is yours — recording a payment, reconciling a ledger — and each module
registers a handler for the event types it cares about. Everything else is answered here.
"""
