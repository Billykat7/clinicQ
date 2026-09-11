# ClinicQ: Engineering Non-negotiables

Five rules. Each is enforced by a **guard test**, so breaking one fails the build rather than relying on
a reviewer noticing. If you need to break one, that is a team discussion and a documented decision,
not a pull request.

## 1. One queue, not two

A ticket joined remotely and a ticket issued at reception draw from **the same sequence**, in arrival
order. No channel gets a systematically better position.

**Why:** a separate "online" line that quietly jumps the physical line is the single fastest way to lose
a clinic's trust. Patients notice within a day and staff stop using the system.

*Enforced by:* Issue 40 (single join service), Issue 79 (cross-channel parity suite).

## 2. Ticket status is written only by `transition_ticket()`

No route, template, worker or script assigns `ticket.status` directly. Every change goes through the
state machine, which rejects illegal transitions with 409 and writes an audit row.

**Why:** four consumers (the board, notifications, reports and the patient's screen) derive their
behaviour from status. One direct write outside the machine and they disagree, silently.

*Enforced by:* Issue 41 and its guard test.

## 3. Every site-scoped query goes through the tenancy helper

Cross-site access returns **404, not 403**, so ids cannot be probed for existence.

**Why:** ClinicQ is multi-tenant from the first pilot. A receptionist at Clinic A must never read a
ticket at Clinic B, and retrofitting that guarantee across forty routes is far more expensive than
holding it from the start.

*Enforced by:* Issue 19 and the cross-tenant test suite.

## 4. The waiting-room board applies privacy server-side

Under `number_only`, a patient name is **not in the response payload at all**, not merely hidden by
CSS. A comment never renders beside a full name without recorded per-visit consent. Every new site is
created with `display_mode = number_only`, and no code path may change that default.

**Why:** a name next to a stated symptom on a public screen is health information about an identifiable
person, displayed publicly, squarely inside what POPIA treats as special personal information. A
mis-styled template must not be able to cause that.

*Enforced by:* Issues 27 and 58, plus a guard test that fails if a template receives raw ticket data.

## 5. Enums on the wire, Johannesburg in the business layer

Wire-safe values are enums, never bare strings. Datetimes are stored as UTC and presented in
`Africa/Johannesburg`. A naive `datetime.now()`, or a status written as a bare string, fails the
build.

**Why:** magic strings drift between six developers; timezone bugs in a queue system are invisible until
a ticket sequence resets at the wrong hour.

*Enforced by:* Issue 4 and `tests/unit/commons/test_conventions.py`, which scans `src/` and
`scripts/db/` and names the file and line of every naive datetime, UTC "now" outside the modules a
standard obliges to use it, and magic status string.

---

## Working agreements

- One issue, one pull request, merged within **3 days** of starting. Split anything bigger.
- Rebase onto `main` daily. No branch lives longer than a week.
- `./scripts/ci-local.sh` green **before** you push.
- Contract before implementation whenever someone else will consume your work.
- Merge behind a feature flag rather than holding a branch open.
- Two issues in progress per person, maximum. A third means something is stuck: label it and say so.
- Review within 24 hours on a weekday, or the backup reviewer may merge.

See [`docs/TEAM/WORKLOAD_SPLIT.md`](TEAM/WORKLOAD_SPLIT.md) for the reasoning behind each of these.
