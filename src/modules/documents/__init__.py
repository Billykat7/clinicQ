"""Unified document storage service (Issue #70).

One bounded context behind which tenant, application, lease and maintenance attachments are
consolidated: a single ``document`` record with consistent access control, integrity (SHA-256
checksums), retention (automatic expiry) and auditing. Bytes live in private storage and leave only
through short-lived signed links, verified against their checksum on the way out.
"""
