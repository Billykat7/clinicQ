# ClinicQ: GitHub Workflow Docs

All development is tracked as **GitHub issues**, grouped into **milestones**. Every issue has a
specification doc in this folder, so scope, context and acceptance criteria live in the repository
rather than in a chat thread. This is a **six-person capstone team**, so the structure exists as much
to keep people from blocking each other as it does to keep the work organised; see
[`docs/TEAM/WORKLOAD_SPLIT.md`](../TEAM/WORKLOAD_SPLIT.md).

## Folder structure

```text
docs/
├── PRODUCT/                      # what we are building and why (14 numbered docs + index.html)
├── PLAN/IMPLEMENTATION_PLAN.md   # the whole plan on one page
├── TEAM/                         # who does what, and what blocks what
├── DEMO/index.html               # visual walkthrough for the team
└── GITHUB/
    ├── README.md                 # this file
    ├── MILESTONES/               # M1 … M14, plus README.md: how to read a milestone
    ├── ISSUES/
    │   ├── README.md             # how to read and pick up an issue, where code goes, open decisions
    │   ├── M1/  … M14/           # Issues 1–109, 197 and 200
    │   └── BACKLOG/              # deliberately parked, post-capstone
    ├── PR/                       # PR_<N>_DESCRIPTION.md per merged issue
    ├── RELEASES/                 # RELEASE_v<major>_<minor>_<patch>.md
    ├── LABELS/labels.yml         # single source of truth for issue labels
    └── RUNNER/                   # self-hosted runner notes
```

## Milestone summary

| Milestone | Focus | Issues | Owner | Status |
|-----------|-------|--------|-------|--------|
| **[M1: Foundation & Local CI](MILESTONES/M1_foundation_local_ci.md)** | Repo, FastAPI skeleton, PostGIS + Redis, migrations, UI shell, `ci-local` | 1–8 | DevOps/QA | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M2: CI/CD, Environments & Team Workflow](MILESTONES/M2_cicd_environments.md)** | Actions CI, GHCR, deploy on tag, CODEOWNERS, labels | 9–14 | DevOps/QA | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M3: Identity, Auth, RBAC & Consent](MILESTONES/M3_identity_auth_rbac.md)** | Staff auth, patient OTP identity, RBAC, site scoping, audit, consent | 15–22 | Backend Lead | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M4: Clinics, Queues & Configuration](MILESTONES/M4_clinics_queues_config.md)** | `sites` + PostGIS, hours, multi-room `queues`, services, display settings, onboarding | 23–30 | Backend Lead | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M5: Discovery & Geolocation](MILESTONES/M5_discovery_geolocation.md)** | Nearby search, sector toggle, map, area fallback, snapshot cache, payment filter | 31–38 | Frontend (Patient) | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M6: Queue Engine Core](MILESTONES/M6_queue_engine_core.md)** ⚠️ | Tickets, join, lifecycle, wait estimates, recall/no-show, transfer, priority | 39–47 | Backend Lead | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M7: Clinic Dashboard](MILESTONES/M7_clinic_dashboard.md)** | Front-desk board, call next, walk-in intake, reorder, room view, settings | 48–55 | Frontend (Clinic) | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M8: Waiting-room Display Monitor](MILESTONES/M8_display_monitor.md)** | Kiosk board, SSE, privacy modes, accessibility, audio call-out, device registry | 56–62 | Frontend (Clinic) | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M9: Notifications & Patient PWA](MILESTONES/M9_notifications_patient_pwa.md)** | Notification service, push, SMS, templates, preferences, ticket page, PWA, QR | 63–71, 200 | Backend (Integrations) | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** ✅ done |
| **[M10: USSD & WhatsApp Channels](MILESTONES/M10_ussd_whatsapp_channels.md)** | Adapter framework, USSD menu, WhatsApp bot, 5 languages, simulators, parity | 72–79 | Backend (Integrations) | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** 📋 planned |
| **[M11: Appointments, Check-in & Patient Care](MILESTONES/M11_appointments_checkin_patient_care.md)** | Slots, booking, reminders, kiosk check-in, proxy booking, chronic, feedback | 80–87 | Backend Lead | 🟩🟩🟩🟩🟩🟩🟩🟩⬜⬜ **75%** 🚧 in progress |
| **[M12: Reporting & Analytics](MILESTONES/M12_reporting_analytics.md)** | Stats worker, reports UI, KPIs, exports, district dashboard, no-show insight | 88–94 | Data & Research | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** 📋 planned |
| **[M13: Security, Privacy & POPIA](MILESTONES/M13_security_privacy_compliance.md)** | Data map, retention, DSAR, hardening, encryption, audit chain, pen test, a11y | 95–101 | DevOps/QA | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** 📋 planned |
| **[M14: Production, Pilot & Go-live](MILESTONES/M14_production_pilot_golive.md)** | Prod infra, shared S3 layout, backups, monitoring, load test, pilot kit, support, UAT, capstone | 102–109, 197 | DevOps/QA + all | 🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜ **11%** 🚧 in progress |

**Total: 111 tracked issues across M1–M14**, plus 6 parked
[backlog items](ISSUES/BACKLOG/).

**Progress:** 🟩🟩🟩🟩🟩🟩🟩⬜⬜⬜ **71%** (79/111 issues) closed · **9 of 14 milestones done**

> ⚠️ **M6 is the critical path.** Six of the seven milestones after it are consumers of the queue
> engine. Anything that delays M6 delays the whole second half of the project, see the
> [dependency analysis](../TEAM/WORKLOAD_SPLIT.md#4-what-blocks-what).

## Delivery order and why

M1–M2 exist so six people can work on one codebase without stepping on each other. M3–M4 build the
things everything else reads: an identity, a site, a queue. **M6 is the heart**, since every surface after
it (dashboard, board, PWA, channels, appointments, reports) is a view onto the queue engine. M5 is
deliberately placed *before* M6 so the patient-facing developer has real work while the queue engine
is still being built, and M13–M14 close the project with the compliance and pilot work that a system
touching health data cannot skip.

## Conventions

- **Issue numbers are sequential across milestones** (1 … 109) so `Closes #N` matches GitHub's
  numbering when issues are created in order. An issue added later takes GitHub's next free number
  (197, after the pull requests up to #196; 200, after #199).
- Branch: `Issue/<N>/<short-slug>`, e.g. `Issue/39/tickets-model-sequence`.
- Commit messages start with the issue: `Issue 39: add concurrency-safe ticket sequence`.
- PR description: `docs/GITHUB/PR/M<MS>/PR_<N>_DESCRIPTION.md`, ending with `Closes #N`.
- **No magic strings:** wire-safe values are enums (Issue 4).
- **Business datetimes are `Africa/Johannesburg`**, stored as UTC (Issue 4).
- **Every site-scoped query goes through the tenancy helper** (Issue 19).
- **Ticket status is written only by `transition_ticket()`** (Issue 41).
- **The waiting-room board applies privacy server-side** (Issue 58).

The last four are the project's non-negotiables, enforced by guard tests rather than by review
attention. They are restated in [`docs/guideline.md`](../guideline.md).

## Definition of Ready / Definition of Done

An issue is **ready** to start when it has a milestone, an owner, its dependencies merged (or an
agreed stub), and acceptance criteria nobody disputes.

An issue is **done** when: acceptance criteria are all ticked; tests cover the new behaviour;
`./scripts/ci-local.sh` is green; the PR description exists and links the issue; a code owner has
approved; and, for anything user-facing, a screenshot or recording is attached.

## Release tags

**The rule: one minor version per milestone, and `v1.0.0` only once the last issue in the last
milestone is closed.** Everything before that is a `0.x` pre-release: the product is not finished, and
the version number should say so honestly rather than flattering the project.

| Tag | Milestone | Cut when |
|-----|-----------|----------|
| `v0.1.0` | M1 Foundation & Local CI | Issues 1–8 closed |
| `v0.2.0` | M2 CI/CD, Environments & Team Workflow | Issues 9–14 closed |
| `v0.3.0` | M3 Identity, Auth, RBAC & Consent | Issues 15–22 closed |
| `v0.4.0` | M4 Clinics, Queues & Configuration | Issues 23–30 closed |
| `v0.5.0` | M5 Discovery & Geolocation | Issues 31–38 closed |
| `v0.6.0` | M6 Queue Engine Core | Issues 39–47 closed |
| `v0.7.0` | M7 Clinic Dashboard | Issues 48–55 closed |
| `v0.8.0` | M8 Waiting-room Display Monitor | Issues 56–62 closed |
| `v0.9.0` | M9 Notifications & Patient PWA | Issues 63–71 and 200 closed |
| `v0.10.0` | M10 USSD & WhatsApp Channels | Issues 72–79 closed |
| `v0.11.0` | M11 Appointments, Check-in & Patient Care | Issues 80–87 closed |
| `v0.12.0` | M12 Reporting & Analytics | Issues 88–94 closed |
| `v0.13.0` | M13 Security, Privacy & POPIA | Issues 95–101 closed |
| `v0.14.0` | M14 Production, Pilot & Go-live | Issues 102–108 closed |
| **`v1.0.0`** | **First official release** | **Issue 109 closed: every issue in every milestone is done** |

### How it works in practice

- **A milestone tag is cut when its last issue closes**, not when the sprint ends. A milestone with one
  issue still open does not get its tag; the work carries into the next sprint and the tag follows it.
- **`0.x.y` patch releases** (`v0.6.1`, `v0.6.2`, …) are for fixes to a milestone that is already tagged:
  a bug found in the queue engine during M7 is `v0.6.1`, not part of `v0.7.0`.
- **Milestone order and tag order are the same.** Even where two milestones overlap in the calendar
  (M12 and M13 both run in sprint 12), the tags are cut in milestone order as each one's last issue closes.
- **`v1.0.0` is the first official release** and it means exactly one thing: **every tracked issue (1–109, 197
  and 200) is closed.** It is not "the pilot went live" or "we demoed it"; those happen inside M14, under `v0.14.0`.
- After `v1.0.0`, normal semantic versioning applies: backlog features are `v1.1.0`+, fixes are
  `v1.0.1`+, and a breaking change is `v2.0.0`.

Tag pushes are the only trigger for deployment (Issue 11), which keeps GitHub Actions minutes inside the
Free plan allowance. Every tag gets a release note in [`RELEASES/`](RELEASES/).

## Pipeline strategy (free-tier aware)

| Trigger | What runs |
|---------|-----------|
| Local, before pushing | `./scripts/ci-local.sh`: ruff, mypy, pytest, docker build |
| Pull request to `main` | CI: lint, type-check, tests against PostGIS + Redis |
| Push to `main` | Nothing automatic |
| Tag `v*.*.*` | Build → GHCR → deploy to staging; production behind manual approval |
| Manual | Smoke check, security scan |

The local gate is the primary one. CI on pull requests is the enforcement, and deployment is
tag-only: the shape that keeps the team inside the 2,000 free Actions minutes a month.
[`docs/CICD/PIPELINES.md`](../CICD/PIPELINES.md) describes each CI job, the one required check
(**CI gate**) and the measured minutes behind that claim.
