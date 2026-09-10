"""Notifications bounded context (Issue #67).

One service that owns delivery of transactional **email and SMS** with per-message delivery
status, replacing the scatter of module-local mail paths. Public surface:

* :func:`src.modules.notifications.service.deliver_email` — the single email send path
  (``src.core.email_send.send`` routes through it); records a ledger row, hands the message to
  SMTP, and tracks its status.
* :func:`src.modules.notifications.service.send_sms` — send an SMS through the pluggable provider.
* :func:`src.modules.notifications.service.run_retry_sweep` — retry due failures with backoff and
  dead-letter after the attempt budget is spent (driven by ``src.core.scheduler``).
* :data:`src.modules.notifications.router.router` — admin delivery-status query + provider webhook.
"""
