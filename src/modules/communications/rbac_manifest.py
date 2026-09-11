"""Communications RBAC manifest: tab-level granularity for messages/announcements/alerts.

Extends the resource tree one level deeper than the module roots, so each console's
Inbox/Sent/Drafts/Deleted tab is independently grantable rather than the module as a whole. Built
on the manifest framework rather than a hand-written seed migration: ``make seed-rbac`` pushes this
tree into the DB catalog idempotently as part of the standard deploy step.

**``drafts`` is grantable but only enforced for ``alerts``.** All three consoles get a ``.drafts``
resource so the catalog is complete and consistent, but alerts drafts are a real, separate table
(``AlertDraft``) with their own endpoints, while messages and announcements *share* one
``MessageDraft`` table and one set of ``/messaging/drafts`` endpoints with no per-console
discriminator. Drafts are author-private regardless of role, so nothing is newly exposed by
leaving that alone; splitting the shared endpoint is a bigger refactor.

**One named action: ``communications.alerts.drafts:send``.** ``POST /alerts/drafts/{id}/send`` is a
real, irreversible dispatch transition — a private draft becomes a broadcast with resolved
recipients — which is the same shape ``sign``/``approve`` have, so it gets the same treatment
alongside (not replacing) the CREATE gate. Announcements has no equivalent: composing *is* sending
there, with no separate publish step to re-gate.

**The shipped grant is ``admin`` over the root.** Without it the consoles exist in the catalog and
open for nobody — the flag would render a rail icon that 403s for every user, including the
administrator who is supposed to hand the grants out. Everyone else is provisioned from the
console.
"""

from src.commons.enums import (
    GrantScope,
    PermissionAction,
    PermissionVerb,
    ScopeShape,
    UserRole,
)
from src.core.rbac import catalog_display_name
from src.core.rbac_manifest import ModuleManifest, NavMeta, ResourceSpec, RoleGrant


def _tab(
    key: str,
    *,
    parent_full_key: str,
    scope: str = GrantScope.BUSINESS.value,
) -> ResourceSpec:
    """One Inbox/Sent/Drafts/Deleted tab under ``parent_full_key`` (e.g. ``communications.messages``).

    ``scope`` is the tier the tab's surface gate requires (Issue #165, M28). It defaults to
    ``business`` — the Announcements and Alerts consoles are whole-business back-office surfaces —
    and the Messages tabs pass ``own``, because that console narrows an own-tier caller to their own
    participant threads (Issue #157) rather than refusing them.
    """
    full_key = f"{parent_full_key}.{key}"
    return ResourceSpec(
        key=key,
        name=catalog_display_name(full_key),
        nav=NavMeta(tab=True, scope=scope),
    )


def _console_tabs(
    parent_full_key: str, *, scope: str = GrantScope.BUSINESS.value
) -> tuple[ResourceSpec, ...]:
    """The four Inbox/Sent/Drafts/Deleted tabs every Communications console ships today.

    ``scope`` is passed straight through to every tab's :class:`NavMeta` — see :func:`_tab`.
    """
    return tuple(
        _tab(section, parent_full_key=parent_full_key, scope=scope)
        for section in ("inbox", "sent", "drafts", "deleted")
    )


MANIFEST = ModuleManifest(
    key="communications",
    name=catalog_display_name("communications"),
    grants=(
        RoleGrant(
            role=UserRole.ADMIN.value,
            resource="communications",
            verb=PermissionVerb.DELETE.value,
            scope=GrantScope.BUSINESS.value,
        ),
    ),
    children=(
        ResourceSpec(
            key="messages",
            name=catalog_display_name("communications.messages"),
            # A thread is a shared conversation: "own" is the threads the caller participates in —
            # the narrowing Issue #157 migrates this console's hand-written branch onto.
            scope_shape=ScopeShape.THREAD_PARTICIPANT,
            # ``own``-tier surface (Issue #165): the console and every one of its tabs open for a
            # portal-scoped caller, who then sees only the threads they participate in. Its
            # siblings below keep the ``business`` default — a portal role's coarse
            # ``communications:CREATE`` grant must not cascade the *staff* announcement and alert
            # boards open, which is exactly what it did between Issues #164 and #165.
            nav=NavMeta(
                icon="mail",
                group="communications",
                href="/admin/messages",
                scope=GrantScope.OWN.value,
            ),
            children=_console_tabs(
                "communications.messages", scope=GrantScope.OWN.value
            ),
        ),
        ResourceSpec(
            key="notifications",
            name=catalog_display_name("communications.notifications"),
            nav=NavMeta(icon="bell", group="communications"),
        ),
        ResourceSpec(
            key="announcements",
            name=catalog_display_name("communications.announcements"),
            # Issue #160 (M28): addressing an announcement to *every user in the system* is its own
            # capability, not a rung on the CRUD ladder — the named-action shape ``sign``/``approve``
            # already use. Replaces the literal ``role != "admin"`` comparison in
            # ``src.modules.messaging.audience``; seeded to ``admin`` alone, so nothing changes for
            # an existing deployment until an operator grants it elsewhere.
            named_actions=(PermissionAction.BROADCAST_GLOBAL.value,),
            nav=NavMeta(icon="megaphone", group="communications"),
            children=_console_tabs("communications.announcements"),
        ),
        ResourceSpec(
            key="alerts",
            name=catalog_display_name("communications.alerts"),
            nav=NavMeta(icon="alert-triangle", group="communications"),
            children=(
                *(
                    tab
                    for tab in _console_tabs("communications.alerts")
                    if tab.key != "drafts"
                ),
                ResourceSpec(
                    key="drafts",
                    name=catalog_display_name("communications.alerts.drafts"),
                    nav=NavMeta(tab=True),
                    named_actions=("send",),
                ),
            ),
        ),
    ),
)
