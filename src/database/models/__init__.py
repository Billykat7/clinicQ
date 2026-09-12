"""ORM models; import so ``Base.metadata`` knows about them for Alembic/create_all.

Every model the kernel owns is imported here unconditionally; everything behind a feature flag
is imported only when that flag is on, because the module itself is not rendered otherwise.
Your own models go in the block at the bottom — Alembic autogenerate only sees what is imported
from this file.
"""

from src.database.models.action import Action
from src.database.models.alert import Alert
from src.database.models.alert_draft import AlertDraft
from src.database.models.alert_recipient import AlertRecipient
from src.database.models.audit_event import AuditEvent
from src.database.models.base import Base, metadata
from src.database.models.document import Document
from src.database.models.effective_role_permission import EffectiveRolePermission
from src.database.models.esign_envelope import EsignEnvelope
from src.database.models.esign_event import EsignEvent
from src.database.models.in_app_notification import InAppNotification
from src.database.models.message import Message
from src.database.models.message_draft import MessageDraft
from src.database.models.message_participant import MessageParticipant
from src.database.models.message_receipt import MessageReceipt
from src.database.models.message_thread import MessageThread
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin
from src.database.models.nav_gate_override import NavGateOverride
from src.database.models.notification import Notification
from src.database.models.notification_preference import NotificationPreference

# ── your models ──────────────────────────────────────────────────────────────
# Add `from src.database.models.<name> import <Model>` here and the name to __all__.
# Alembic autogenerate only sees what this file imports: a model missing from it reflects
# as a table to *drop*.
from src.database.models.patient import Patient
from src.database.models.patient_consent import PatientConsent, PatientConsentEvent
from src.database.models.paystack_event import PaystackEvent
from src.database.models.permission import Permission
from src.database.models.permission_audit_log import PermissionAuditLog
from src.database.models.permission_usage import (
    USAGE_WINDOW_ID,
    PermissionUsage,
    PermissionUsageWindow,
)
from src.database.models.rbac_role import RbacRole
from src.database.models.refresh_token import RefreshToken
from src.database.models.resource import Resource
from src.database.models.resource_descendant import ResourceDescendant
from src.database.models.role_hierarchy import RoleHierarchy
from src.database.models.role_permission import RolePermission
from src.database.models.staff_invitation import StaffInvitation
from src.database.models.stripe_event import StripeEvent
from src.database.models.user import User
from src.database.models.user_role_assignment import UserRoleAssignment
from src.database.models.widget import Widget

__all__ = [
    "USAGE_WINDOW_ID",
    "Action",
    "ActiveMixin",
    "Alert",
    "AlertDraft",
    "AlertRecipient",
    "AuditEvent",
    "Base",
    "Document",
    "EffectiveRolePermission",
    "EsignEnvelope",
    "EsignEvent",
    "InAppNotification",
    "Message",
    "MessageDraft",
    "MessageParticipant",
    "MessageReceipt",
    "MessageThread",
    "NavGateOverride",
    "Notification",
    "NotificationPreference",
    "Patient",
    "PatientConsent",
    "PatientConsentEvent",
    "PaystackEvent",
    "Permission",
    "PermissionAuditLog",
    "PermissionUsage",
    "PermissionUsageWindow",
    "RbacRole",
    "RefreshToken",
    "Resource",
    "ResourceDescendant",
    "RoleHierarchy",
    "RolePermission",
    "SoftDeleteMixin",
    "StaffInvitation",
    "StripeEvent",
    "TimestampMixin",
    "User",
    "UserRoleAssignment",
    "Widget",
    "metadata",
]
