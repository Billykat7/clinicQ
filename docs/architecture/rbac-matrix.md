# RBAC matrix: what each role may do

> **In short:** ClinicQ's five roles and the kernel's two, against every resource in the catalog.
> The table is generated from a database seeded exactly as a deployment is seeded, so it is what
> the running system decides, not what someone remembered. Regenerate it with `make rbac-matrix`;
> `tests/test_rbac_matrix.py` fails when it is stale.

## How to read a cell

`update · assigned` means: the role holds `update` (and so `create` and `read`, because verbs are
cumulative: `read < create < update < delete`) on that resource, at the `assigned` tier. The tier
answers *whose rows*:

| Tier | Reaches | Who has it |
|---|---|---|
| `own` | the rows the caller is the subject of: a patient's own record; for a nurse, the queues assigned to them | `patient`, and the nurse's call-next grant |
| `assigned` | the rows at the sites the caller holds a role at (`user_roles` with `scope_type='site'`) | `receptionist`, `nurse_doctor`, `clinic_manager` |
| `business` | every clinic. A `platform_admin` reading a clinic it is not assigned to does so explicitly and is audited (Issue 19) | `platform_admin`, `admin`, `user` |

`—` means no access at all. A grant on a parent cascades to its children (a clinic manager's
`update` on `sites` reaches `sites.display`), unless a child grant or a deny says otherwise.

## Two gates

RBAC (this table) answers *what* a role may do. The site guard (Issue 19) answers *which rows*:
a receptionist holds `update` on `queues.call`, and the guard is what keeps that to the queues at
their own clinic, answering 404 for another clinic's. Every protected API route declares its gate
(`tests/unit/security/test_api_route_gates.py`), and a template hides an action with
`can(resource, verb)`, the same check, as a courtesy rather than the control.

## Where the grants come from

Every grant is declared next to the resource it gates, in a module's `rbac_manifest.py`
(`src/modules/sites`, `src/modules/queues`, `src/modules/patients`, and the kernel's), plus the
kernel's system grants (`default_role_permissions`). `make seed-rbac` applies them, idempotently,
and the deploy sequence runs it right after the migrations (decision 6 in
`docs/GITHUB/ISSUES/README.md`). An operator can change a grant from `/admin/rbac`; this table
shows what ships.

## The matrix

<!-- BEGIN GENERATED RBAC MATRIX -->

| Resource | `patient` | `receptionist` | `nurse_doctor` | `clinic_manager` | `platform_admin` | `admin` | `user` |
|---|---|---|---|---|---|---|---|
| `communications` | — | — | — | — | — | delete · business | — |
| `communications.alerts` | — | — | — | — | — | delete · business | — |
| `communications.alerts.deleted` | — | — | — | — | — | delete · business | — |
| `communications.alerts.drafts` | — | — | — | — | — | delete · business | — |
| `communications.alerts.inbox` | — | — | — | — | — | delete · business | — |
| `communications.alerts.sent` | — | — | — | — | — | delete · business | — |
| `communications.announcements` | — | — | — | — | — | delete · business | — |
| `communications.announcements.deleted` | — | — | — | — | — | delete · business | — |
| `communications.announcements.drafts` | — | — | — | — | — | delete · business | — |
| `communications.announcements.inbox` | — | — | — | — | — | delete · business | — |
| `communications.announcements.sent` | — | — | — | — | — | delete · business | — |
| `communications.messages` | — | — | — | — | — | delete · business | — |
| `communications.messages.deleted` | — | — | — | — | — | delete · business | — |
| `communications.messages.drafts` | — | — | — | — | — | delete · business | — |
| `communications.messages.inbox` | — | — | — | — | — | delete · business | — |
| `communications.messages.sent` | — | — | — | — | — | delete · business | — |
| `communications.notifications` | — | — | — | — | — | delete · business | — |
| `dashboard` | — | read · business | read · business | read · business | read · business | delete · business | read · business |
| `document.signature` | — | — | — | — | — | — | — |
| `documents` | — | — | — | — | — | — | — |
| `logs` | — | — | — | — | — | delete · business | — |
| `patients` | — | — | — | — | — | — | — |
| `patients.self` | update · own | — | — | — | — | — | — |
| `queues` | — | read · assigned | read · assigned | delete · assigned | read · business | — | — |
| `queues.call` | — | update · assigned | update · own | delete · assigned | read · business | — | — |
| `queues.tickets` | — | update · assigned | update · own | delete · assigned | read · business | — | — |
| `queues.tickets.priority` | — | update · assigned | update · own | delete · assigned | read · business | — | — |
| `rbac` | — | — | — | — | — | delete · business | — |
| `reports` | — | — | — | — | — | — | — |
| `sites` | — | — | — | update · assigned | delete · business | — | — |
| `sites.display` | — | read · assigned | — | update · assigned | delete · business | — | — |
| `sites.profile` | — | read · assigned | read · assigned | update · assigned | delete · business | — | — |
| `sites.reports` | — | — | — | update · assigned | delete · business | — | — |
| `sites.settings` | — | — | — | update · assigned | delete · business | — | — |
| `sites.staff` | — | read · assigned | read · assigned | delete · assigned | delete · business | — | — |
| `users` | — | — | — | — | — | delete · business | — |
| `widgets` | — | — | — | — | — | delete · business | — |
| `widgets.archive` | — | — | — | — | — | delete · business | — |

<!-- END GENERATED RBAC MATRIX -->
