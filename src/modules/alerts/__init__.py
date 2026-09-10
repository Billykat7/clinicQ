"""Staff-authored and system-raised alerts: a one-way broadcast (Issue #132 follow-up).

Mirrors the shape ``messaging`` established for announcements — the same
:class:`~src.modules.messaging.enums.AudienceType` resolution, drafts, Sent/Inbox — minus the
reply half, since an alert is never a conversation.
"""
