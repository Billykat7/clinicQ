"""Cross-cutting enumerations (wire-safe values for APIs and events)."""

from __future__ import annotations

from enum import Enum, StrEnum


class RateLimitBackendKind(StrEnum):
    """Where the sliding-window rate limiters keep their state (Issue #179, M30).

    - ``MEMORY``: process-local windows. Correct and complete for a single-worker development or
      CI run, and the default, so nothing new is required to run the app. Under horizontal scaling
      the effective budget multiplies by the worker count — pen-test finding **F-02**.
    - ``REDIS``: one window shared by every worker and instance, so a budget means what it says.
      Selected only by configuration; when the store is unreachable the limiter degrades to the
      in-process window rather than refusing traffic (see ``src.core.rate_limit``).
    """

    MEMORY = "memory"
    REDIS = "redis"


class AppEnvironment(StrEnum):
    """Process environment for config and docs exposure."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class DbSchema(StrEnum):
    """PostgreSQL schema owning this project's tables (env: ``DB_SCHEMA``).

    The platform host runs several products against one PostgreSQL database, so each
    one keeps its tables in a schema of its own rather than in ``public`` (which is
    left to PostGIS). ``PROPERTIES`` is this project's schema; ``alembic/versions``
    creates it and puts every table — and Alembic's own ``alembic_version`` — inside.
    """

    CLINICQ = "clinicq"


class HealthStatus(StrEnum):
    """Aggregated health used by orchestration probes."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class DependencyStatus(StrEnum):
    """Per-dependency result behind the readiness probe (Issue #65).

    ``SKIPPED`` means the dependency is not configured for this deployment (e.g. S3
    log shipping is off), so it neither passes nor fails and is excluded from the
    aggregated readiness verdict.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    SKIPPED = "skipped"


class BoundedContext(StrEnum):
    """One entry per ``src.modules`` package; aligns with ``src/contracts/openapi/*.yaml``.

    Add a member when you add a module — :class:`src.commons.schemas.ServiceMeta` types its
    ``context`` field on this enum, so a module with no member here cannot report its own health.
    """

    ACCOUNT = "account"
    AUDIT = "audit"
    NOTIFICATIONS = "notifications"
    MESSAGING = "messaging"
    ALERTS = "alerts"
    DOCUMENTS = "documents"
    WIDGETS = "widgets"
    PATIENTS = "patients"
    STAFF = "staff"
    SITES = "sites"
    QUEUES = "queues"
    DISCOVERY = "discovery"


class LogLevel(StrEnum):
    """Log level names (config); values match ``logging`` level names."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(StrEnum):
    """How the console handler writes a record (env: ``LOG_FORMAT``; Issue 6).

    ``JSON`` is one object per line (NDJSON), the format a log pipeline and ``jq`` read, and the
    default everywhere. ``TEXT`` is the kernel's human-readable line, for a developer who prefers
    it locally; the same context and the same redaction apply to both.
    """

    JSON = "json"
    TEXT = "text"


class S3LogPath(StrEnum):
    """S3 log path segment (``api`` / ``web``) under ``{env}/logs/{log_type}/``."""

    API = "api"
    WEB = "web"
    WORKER = "worker"


class S3LogType(StrEnum):
    """S3 ``log_type`` segment under ``{env}/logs/{log_type}/`` (severity bucket)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class S3LogListingLevel(StrEnum):
    """Log-browser filter for the admin S3 log listing API.

    ``INFO`` / ``WARNING`` / ``ERROR`` match the ``log_type`` segment under
    ``{env}/logs/``. ``ALL`` is API-only: list with prefix ``{env}/logs/`` (all
    log types), so it is not itself a stored path segment.
    """

    ALL = "all"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogsPageSize(int, Enum):
    """Allowed page sizes for the admin S3 log listing API."""

    XS = 10
    SMALL = 25
    MEDIUM = 50
    LARGE = 100


class HttpSecurityResponseEvent(StrEnum):
    """Labels for HTTP auth failures in request logs.

    Emitted as JSON in ``SECURITY_HTTP`` lines and as ``security_http_event`` on the
    log record so log aggregators can key on authentication anomalies.
    """

    UNAUTHORIZED = "http_401_unauthorized"
    FORBIDDEN = "http_403_forbidden"


class SecurityAuditOutcome(StrEnum):
    """Outcome of a security-relevant action for audit/HTTP security records."""

    SUCCESS = "success"
    FAILURE = "failure"


class SecurityAuditEvent(StrEnum):
    """Labels for security-relevant domain actions written to the audit log.

    Emitted as the ``event`` field of a ``SECURITY_AUDIT`` JSON line (and as
    ``security_audit_event`` on the log record) so log aggregators can key on them.
    """

    TENANT_DOCUMENT_DOWNLOAD = "tenant_document_download"
    # Move-in / move-out of a tenancy (Issue #35). MOVE_OUT_OVERRIDE records the exceptional
    # case of a move-out forced through despite an outstanding balance, with its reason.
    TENANCY_MOVE_IN = "tenancy_move_in"
    TENANCY_MOVE_OUT = "tenancy_move_out"
    TENANCY_MOVE_OUT_OVERRIDE = "tenancy_move_out_override"
    # Maintenance requests (Issue #47). SUBMITTED records a tenant raising a request against a
    # unit they occupy (the ownership-gated action); ATTACHMENT_DOWNLOAD records a private
    # photo/video attachment being served through a signed link.
    MAINTENANCE_REQUEST_SUBMITTED = "maintenance_request_submitted"
    MAINTENANCE_ATTACHMENT_DOWNLOAD = "maintenance_attachment_download"
    # Work orders (Issue #48). ASSIGNED records a job being handed to a vendor with a scheduled
    # date (the vendor and tenant are additionally emailed post-commit — Issue #51); COMPLETED
    # records a job finished with its final cost; CANCELLED records a job called off before
    # completion, with who cancelled it and why.
    WORK_ORDER_ASSIGNED = "work_order_assigned"
    WORK_ORDER_COMPLETED = "work_order_completed"
    WORK_ORDER_CANCELLED = "work_order_cancelled"
    # Cost approval (Issue #50). APPROVED / REJECTED record an owner (or a manager acting for them)
    # deciding on a work-order estimate that is above the property's approval threshold, with the
    # deciding actor and the recorded reason; both are keyed on by log aggregators.
    WORK_ORDER_APPROVED = "work_order_approved"
    WORK_ORDER_REJECTED = "work_order_rejected"
    # Inspections (Issue #69). COMPLETED records a move-in/move-out/annual inspection being signed
    # off (the point the immutable report is generated and stamped); PHOTO_DOWNLOAD records a
    # private inspection photo being served through a signed link; DEDUCTION_APPROVED /
    # DEDUCTION_REJECTED record the once-only decision on a proposed deposit deduction, with the
    # deciding actor — APPROVED is also the point a charge is posted to the tenant's lease (M7).
    INSPECTION_COMPLETED = "inspection_completed"
    INSPECTION_PHOTO_DOWNLOAD = "inspection_photo_download"
    INSPECTION_DEDUCTION_APPROVED = "inspection_deduction_approved"
    INSPECTION_DEDUCTION_REJECTED = "inspection_deduction_rejected"
    # Document storage service (Issue #70). DOWNLOAD records a document's bytes being served
    # through a signed, expiring link — the only read path — carrying the actor the link was
    # minted for, the document id and a timestamp. RETENTION_DELETED records the retention sweep
    # purging an expired document: the bytes are removed and the row tombstoned, so a deletion is
    # never silent. Both are keyed on by log aggregators.
    DOCUMENT_DOWNLOAD = "document_download"
    DOCUMENT_RETENTION_DELETED = "document_retention_deleted"
    # E-signature integration (Issue #71). ENVELOPE_SENT records a lease/addendum being sent for
    # signature; SIGNED / DECLINED / EXPIRED record the terminal outcome applied from a verified,
    # idempotent provider webhook; PAPER_SIGNATURE records the manual fallback used when the
    # provider is unavailable (a lease can still be signed on paper). WEBHOOK_REJECTED records a
    # webhook whose signature failed verification (a FAILURE outcome) — the provider's shared
    # secret and payload are *never* logged, only that a call was rejected. All are keyed on by
    # log aggregators.
    ESIGN_ENVELOPE_SENT = "esign_envelope_sent"
    ESIGN_ENVELOPE_SIGNED = "esign_envelope_signed"
    ESIGN_ENVELOPE_DECLINED = "esign_envelope_declined"
    ESIGN_ENVELOPE_EXPIRED = "esign_envelope_expired"
    ESIGN_PAPER_SIGNATURE = "esign_paper_signature"
    ESIGN_WEBHOOK_REJECTED = "esign_webhook_rejected"
    # Notification preferences (Issue #72). UNSUBSCRIBED records a recipient turning off a
    # non-essential notification category through a login-free unsubscribe link — the actor is the
    # email the signed link was minted for (never resolved by enumerating accounts), and the row
    # names only the category, so an unsubscribe is auditable without leaking who does or does not
    # have an account. Keyed on by log aggregators.
    NOTIFICATION_UNSUBSCRIBED = "notification_unsubscribed"
    # Sessions (Issue 16). REFRESH_TOKEN_REUSE records a refresh token presented again after it was
    # rotated, outside the concurrent-refresh grace window: the family it belongs to is revoked,
    # because the server cannot tell whether the thief or the owner holds the newest token. The
    # line names the user id and the family (session) id, never the token.
    REFRESH_TOKEN_REUSE = "refresh_token_reuse"


class UserRole(StrEnum):
    """User role for RBAC: one vocabulary for staff and patients alike (Issues 4, 18).

    A role in a token, a grant or an assignment is always a member of this one type.

    - ``user`` and ``admin`` are the kernel's system roles. ``user`` is the base signed-in role
      (the dashboard, the notification bell); ``admin`` configures the platform (users, RBAC, logs)
      and is the one role seeded scope-exempt, so a deployment always has a way back in.
    - ClinicQ's five roles (Issue 18), each seeded from the module manifests by ``make seed-rbac``:

      * ``patient``: a phone number with a session (Issue 17), acting on their own record only;
      * ``receptionist``: runs the front desk of their clinic: issues and moves tickets, calls next
        on any queue there, reads the clinic's settings but cannot change them;
      * ``nurse_doctor``: calls next on the queues they are assigned to, and nothing wider;
      * ``clinic_manager``: runs their clinic: its profile, settings, display mode, staff, queues
        and reports;
      * ``platform_admin``: the ClinicQ operator's cross-clinic role: onboarding, support,
        aggregate reports. Not scope-exempt: reading another clinic is explicit and audited
        (Issue 19), never implicit.

    A staff member's clinic is a role held **at a site** (``user_roles`` with ``scope_type='site'``,
    Issue 15), never a column. RBAC decides *what* a role may do; the grant's tier and the site guard
    decide *whose* rows (Issue 19).
    """

    USER = "user"
    ADMIN = "admin"
    PATIENT = "patient"
    RECEPTIONIST = "receptionist"
    NURSE_DOCTOR = "nurse_doctor"
    CLINIC_MANAGER = "clinic_manager"
    PLATFORM_ADMIN = "platform_admin"


# --------------------------------------------------------------------------------------
# ClinicQ vocabulary (Issue 4). The wire values every module shares: a site's sector, a ticket's
# status and source, and what the waiting-room board may show. Models use them from their own
# issues (23, 27, 39, 41); ``GET /api/v1/reference/enums`` publishes them in the OpenAPI schema.
# Adding a member is a contract change: a USSD menu, a board and a report all read these values.
# --------------------------------------------------------------------------------------


class SiteSector(StrEnum):
    """Whether a site (one clinic) is a public facility or a private practice. ``sites.sector``."""

    PUBLIC = "public"
    PRIVATE = "private"


class SiteStatus(StrEnum):
    """Where one clinic is in its listing lifecycle. Stored in ``site.status`` (Issues 23, 29).

    The lifecycle is ``DRAFT`` → ``PENDING_VERIFICATION`` → ``VERIFIED``, with ``SUSPENDED``
    reachable from any of them and reversible. Issue 29 owns the transitions and the platform
    admin's verification queue; Issue 23 owns the column, because a site that exists before the
    workflow does must already say whether patients may see it.

    - ``DRAFT``: created by an operator and not yet submitted. Invisible to patients.
    - ``PENDING_VERIFICATION``: submitted and waiting for a platform admin. Invisible to patients,
      reachable by direct link so the clinic can check its own entry.
    - ``VERIFIED``: checked by a platform admin. **The only status discovery ever returns.**
    - ``SUSPENDED``: switched off by a platform admin. Invisible, and it stops accepting joins at
      once (Issue 29).
    """

    DRAFT = "draft"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    SUSPENDED = "suspended"


#: The status a site is created with on every code path that is not the public registration form
#: (Issue 23). A site an operator types in is a draft until somebody submits it.
SITE_DEFAULT_STATUS: SiteStatus = SiteStatus.DRAFT

#: The only statuses a patient-facing surface may return: discovery, the channel menus and the
#: clinic detail page all read this rather than naming the member (Issues 29, 31).
SITE_PUBLICLY_VISIBLE_STATUSES: frozenset[SiteStatus] = frozenset({SiteStatus.VERIFIED})


class SectorFilter(StrEnum):
    """Which clinics a patient asked to see: the discovery toggle (Issues 31, 32).

    Separate from :class:`SiteSector` on purpose. A clinic *is* public or private; a search can also
    ask for **all**, and a stored ``sector`` column must never be able to hold that third value.

    - ``PUBLIC``: public facilities only.
    - ``PRIVATE``: private practices only.
    - ``ALL``: both, which is the default a patient lands on.
    """

    PUBLIC = "public"
    PRIVATE = "private"
    ALL = "all"

    @property
    def sector(self) -> SiteSector | None:
        """The one sector this filter narrows to, or ``None`` for :attr:`ALL`."""
        return None if self is SectorFilter.ALL else SiteSector(self.value)


class SaProvince(StrEnum):
    """South Africa's nine provinces, spelled as the Department of Health and OpenStreetMap do.

    A site's province is part of its address and of every district report (M12), so it is a closed
    vocabulary rather than typed text: "KZN", "Kwazulu Natal" and "KwaZulu-Natal" are one province
    and must group as one.
    """

    EASTERN_CAPE = "Eastern Cape"
    FREE_STATE = "Free State"
    GAUTENG = "Gauteng"
    KWAZULU_NATAL = "KwaZulu-Natal"
    LIMPOPO = "Limpopo"
    MPUMALANGA = "Mpumalanga"
    NORTH_WEST = "North West"
    NORTHERN_CAPE = "Northern Cape"
    WESTERN_CAPE = "Western Cape"


class GeocodingProvider(StrEnum):
    """Which service turns a typed address into a coordinate, server-side (Issue 23).

    The browser never calls a geocoder: the Content-Security-Policy allows ``connect-src 'self'``
    and nothing else, so the address goes to ClinicQ and ClinicQ asks the provider. The default is
    ``NONE`` — a deployment that has not chosen a provider asks the operator for the coordinate
    instead of silently reaching out to somebody's service.

    - ``NONE``: no lookup. ``POST /sites/geocode`` answers 503 and site creation needs a coordinate.
    - ``NOMINATIM``: the OpenStreetMap search API (or a self-hosted instance), which requires a
      descriptive ``User-Agent`` and asks for at most one request a second.
    """

    NONE = "none"
    NOMINATIM = "nominatim"


class DiscoverySort(StrEnum):
    """How a discovery list is ordered (Issue 32).

    - ``NEAREST``: by distance, the default.
    - ``SHORTEST_QUEUE``: by the number waiting; clinics whose length is not measured come last,
      because an unknown queue is not an empty one.
    """

    NEAREST = "nearest"
    SHORTEST_QUEUE = "shortest_queue"


class SnapshotReadOutcome(StrEnum):
    """Where a queue length came from on a discovery read (Issue 36). The label of a metric.

    - ``CACHE_HIT``: a fresh snapshot in Redis.
    - ``TABLE``: Redis had nothing fresh; a fresh ``site_queue_snapshot`` row did.
    - ``RECOUNTED``: neither was fresh, so the queue was counted from the source of truth.
    """

    CACHE_HIT = "cache_hit"
    TABLE = "table"
    RECOUNTED = "recounted"


class MedicalAidScheme(StrEnum):
    """The medical schemes a private clinic may say it accepts: a controlled list (Issue 37).

    A directory **tag** a clinic reports about itself, never an eligibility or claims check (that is
    backlog item 1). Closed so that a patient's filter and a clinic's declaration meet on the same
    value ("GEMS", "Gems" and "Government Employees Medical Scheme" are one scheme). The open and
    restricted schemes with the most members on the Council for Medical Schemes' register, plus
    ``OTHER``, which carries the scheme's name as free text. The words shown for each live in
    ``src.modules.sites.payment_profile.SCHEME_LABELS``.
    """

    DISCOVERY_HEALTH = "discovery_health"
    GEMS = "gems"
    BONITAS = "bonitas"
    MOMENTUM_HEALTH = "momentum_health"
    MEDSHIELD = "medshield"
    BESTMED = "bestmed"
    FEDHEALTH = "fedhealth"
    MEDIHELP = "medihelp"
    PROFMED = "profmed"
    KEYHEALTH = "keyhealth"
    SIZWE_HOSMED = "sizwe_hosmed"
    COMPCARE = "compcare"
    BANKMED = "bankmed"
    POLMED = "polmed"
    LA_HEALTH = "la_health"
    OTHER = "other"


class DiscoveryEventKind(StrEnum):
    """What a discovery analytics event records (Issue 38). Stored in ``discovery_event.kind``.

    A view and a join are separate events on purpose, so a clinic can ask how many people looked
    and did not come.

    - ``SEARCH_PERFORMED``: a list of clinics was shown. Carries no clinic.
    - ``CLINIC_VIEWED``: one clinic's detail was opened.
    - ``JOIN_STARTED``: the patient began joining a queue at that clinic (Issue 40 records it).
    - ``JOIN_COMPLETED``: they got a ticket (Issue 40 records it).
    """

    SEARCH_PERFORMED = "search_performed"
    CLINIC_VIEWED = "clinic_viewed"
    JOIN_STARTED = "join_started"
    JOIN_COMPLETED = "join_completed"


class DiscoveryChannel(StrEnum):
    """Where a discovery event came from (Issue 38). Stored in ``discovery_event.channel``."""

    WEB = "web"
    API = "api"
    USSD = "ussd"
    WHATSAPP = "whatsapp"


class AreaKind(StrEnum):
    """What kind of place an area is. Stored in ``area.kind`` (Issue 34).

    Read from OpenStreetMap's ``place`` tag, folded into the five kinds a patient would recognise.
    South African townships are tagged ``suburb`` or ``town`` in OSM, so there is no separate
    member for them: Soweto is a city, Orlando West a suburb.

    - ``CITY``: Johannesburg, Durban, Soweto.
    - ``TOWN``: Thembisa, Pinetown.
    - ``SUBURB``: Hillbrow, KwaMashu, Orlando West.
    - ``VILLAGE``: a rural settlement.
    - ``NEIGHBOURHOOD``: a named part of a suburb (OSM ``quarter`` and ``neighbourhood``).
    """

    CITY = "city"
    TOWN = "town"
    SUBURB = "suburb"
    VILLAGE = "village"
    NEIGHBOURHOOD = "neighbourhood"


class DistanceBasis(StrEnum):
    """What a discovery distance was measured from (Issues 31, 34).

    - ``POSITION``: the patient's own position, a GPS fix. As exact as the fix.
    - ``AREA_CENTROID``: the middle of a suburb or town the patient typed. **Approximate**, and
      labelled so everywhere it appears: the patient may live at the edge of Soweto, 10 km from
      its middle.
    """

    POSITION = "position"
    AREA_CENTROID = "area_centroid"

    @property
    def approximate(self) -> bool:
        """Whether a distance measured from here must be shown as approximate."""
        return self is DistanceBasis.AREA_CENTROID


class QueueKind(StrEnum):
    """What kind of line a queue is. Stored in ``queue.kind`` (Issue 25).

    A real clinic visit is triage, then a consulting room, then the pharmacy window, so a site runs
    several named queues and a patient joins one of them. The kind is what lets a board, a report
    and a channel menu treat "the pharmacy" the same way at every clinic without matching on a name
    somebody typed.

    - ``TRIAGE``: the first stop, where a nurse decides how urgent the visit is.
    - ``CONSULTATION``: a doctor's or nurse's room.
    - ``PHARMACY``: the medicine window, including chronic medication collection.
    - ``OTHER``: anything else a clinic runs — a dressing room, a records desk, an X-ray queue.
    """

    TRIAGE = "triage"
    CONSULTATION = "consultation"
    PHARMACY = "pharmacy"
    OTHER = "other"


class ServiceCategory(StrEnum):
    """What kind of care a clinic service is. Stored in ``clinic_service.category`` (Issue 26).

    A closed vocabulary rather than typed text, because a district report (M12) groups by it across
    clinics that each name the same service differently: "ARV collection", "HIV treatment" and
    "chronic ARVs" are one category.

    - ``CONSULTATION``: seeing a nurse or a doctor about something new.
    - ``CHRONIC``: collecting repeat medication for a long-term condition.
    - ``MATERNAL``: antenatal and postnatal care, and family planning.
    - ``CHILD_HEALTH``: immunisation, growth monitoring, under-fives.
    - ``HIV_TB``: testing, counselling, initiation and treatment.
    - ``SCREENING``: blood pressure, glucose, cervical and other screening.
    - ``PHARMACY``: dispensing at the medicine window.
    - ``OTHER``: anything a clinic offers that none of the above describes.
    """

    CONSULTATION = "consultation"
    CHRONIC = "chronic"
    MATERNAL = "maternal"
    CHILD_HEALTH = "child_health"
    HIV_TB = "hiv_tb"
    SCREENING = "screening"
    PHARMACY = "pharmacy"
    OTHER = "other"


class TicketStatus(StrEnum):
    """Where one ticket is in its lifecycle. Stored in ``tickets.status``.

    Written only by ``transition_ticket()`` (non-negotiable 2, Issue 41), which owns the legal
    transitions; this enum is only the vocabulary.

    - ``WAITING``: in the queue, not yet called. Every ticket starts here, from any source.
    - ``CALLED``: called to a room or counter; the board shows it as now serving.
    - ``RECALLED``: called, did not arrive within the site's timeout, and was called once more
      (Issue 43).
    - ``IN_PROGRESS``: the patient is being seen.
    - ``DONE``: seen and finished.
    - ``NO_SHOW``: recalled and still absent; the slot is freed.
    - ``CANCELLED``: withdrawn by the patient or by staff before being seen.
    - ``TRANSFERRED``: closed in this queue because the visit moved on to another (triage to
      doctor to pharmacy); the next queue has a ticket of its own (Issue 45).
    """

    WAITING = "waiting"
    CALLED = "called"
    RECALLED = "recalled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"
    TRANSFERRED = "transferred"


# Statuses a ticket never leaves: a correction is a new ticket, not an edit (Issue 41). Defined here,
# next to the enum, so the board, notifications and reports agree on what "finished" means.
TICKET_TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.DONE,
        TicketStatus.NO_SHOW,
        TicketStatus.CANCELLED,
        TicketStatus.TRANSFERRED,
    }
)

#: Statuses a ticket is still in the queue's day with: the board's rows, and what a patient can
#: hold only one of per queue (Issue 40). Everything that is not terminal, derived rather than
#: listed so the two sets cannot disagree.
TICKET_ACTIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    set(TicketStatus) - TICKET_TERMINAL_STATUSES
)


class TicketSource(StrEnum):
    """How a ticket was created. Stored in ``tickets.source``.

    Every source draws from the same sequence (non-negotiable 1): the source is recorded for
    channel-mix reporting (Issue 79), never to order the queue.
    """

    WEB = "web"
    USSD = "ussd"
    WHATSAPP = "whatsapp"
    WALK_IN = "walk_in"


class EstimateConfidence(StrEnum):
    """How much a wait estimate can be trusted (Issue 42). Shown beside every range.

    - ``LOW``: too few recent visits on this queue to measure; built from the queue's expected
      minutes and labelled approximate.
    - ``MEDIUM``: measured from enough recent visits, but few or scattered.
    - ``HIGH``: measured from plenty of recent visits at around this hour that agree with each other.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EstimateBasis(StrEnum):
    """What a wait estimate was built from (Issue 42).

    - ``OBSERVED``: the queue's own recent visits.
    - ``EXPECTED``: the queue's configured expected minutes (Issues 25, 26), until enough visits exist.
    """

    OBSERVED = "observed"
    EXPECTED = "expected"


class ActorKind(StrEnum):
    """Who moved a ticket (Issue 41). Recorded as the audit row's ``actor_role`` for a transition.

    The trail has to answer "did a person do this, or did the system?" without reading a free-text
    actor name: a no-show marked by the recall timer (Issue 43) and one marked by a receptionist
    are different facts, and a report on overrides (Issue 90) must count only the second.

    - ``STAFF``: a signed-in staff member at the clinic.
    - ``PATIENT``: the patient, acting on their own ticket (a cancellation, Issue 44).
    - ``SYSTEM``: a scheduled job, never a person.
    """

    STAFF = "staff"
    PATIENT = "patient"
    SYSTEM = "system"


class JoinRefusal(StrEnum):
    """Why a join was refused (Issue 40). The ``code`` suffix on the wire: ``queue.join.<value>``.

    Every channel renders the same refusal, so a USSD menu, a WhatsApp reply and the web page can
    each say it their own way while agreeing on what happened. The sentence a patient reads travels
    with it; this is the machine-readable half.

    - ``CLINIC_CLOSED``: the clinic is closed now, suspended, or not taking patients through ClinicQ.
    - ``QUEUE_CLOSED``: this queue is deactivated or removed.
    - ``WALK_IN_ONLY``: the queue takes walk-ins only, and the join came from a phone.
    - ``QUEUE_FULL``: the queue has issued its daily capacity.
    - ``SITE_DAILY_CAP``: the clinic has taken as many remote joins today as it allows.
    - ``RATE_LIMITED``: too many joins from one number, or from one address on the web.
    """

    CLINIC_CLOSED = "clinic_closed"
    QUEUE_CLOSED = "queue_closed"
    WALK_IN_ONLY = "walk_in_only"
    QUEUE_FULL = "queue_full"
    SITE_DAILY_CAP = "site_daily_cap"
    RATE_LIMITED = "rate_limited"


class PatientChannel(StrEnum):
    """The channel a patient reached ClinicQ through (Issue 17).

    The same values as :class:`TicketSource`, because they are the same four doors: a patient on
    the web, on a USSD menu, on WhatsApp, or at the reception desk. Recorded on the patient as the
    last channel used, and (Issue 21) on every consent as the channel it was given on.
    """

    WEB = "web"
    USSD = "ussd"
    WHATSAPP = "whatsapp"
    WALK_IN = "walk_in"


#: The channels whose gateway vouches for the caller's number (Issue 17). A USSD session and a
#: WhatsApp conversation arrive with the MSISDN the mobile network (or Meta) authenticated, so the
#: number is trusted without a second OTP. A deliberate decision: see ``src/modules/patients``.
GATEWAY_TRUSTED_CHANNELS: frozenset[PatientChannel] = frozenset(
    {PatientChannel.USSD, PatientChannel.WHATSAPP}
)


class ConsentPurpose(StrEnum):
    """What a patient is being asked to agree to (Issue 21). Consent is per purpose, never one flag.

    Each is a separate question because they have different answers: someone may be glad to be
    texted and not want their name on a screen in a waiting room. Every purpose **defaults to the
    most private answer** (not granted), on every channel.

    - ``DISPLAY_NAME``: show my name on the waiting-room board instead of my ticket number.
    - ``DISPLAY_COMMENT``: show the reason I gave beside it. Health information on a public screen,
      so it is asked separately and means nothing without ``DISPLAY_NAME``.
    - ``NOTIFICATIONS``: send me messages about my place in the queue. The code that proves my
      number is not this: I asked for that one by typing my number (see ``has_consent``).
    - ``FEEDBACK_SURVEY``: ask me afterwards how the visit went.
    """

    DISPLAY_NAME = "display_name"
    DISPLAY_COMMENT = "display_comment"
    NOTIFICATIONS = "notifications"
    FEEDBACK_SURVEY = "feedback_survey"


class OtpSubjectKind(StrEnum):
    """What a one-time code proves control of (Issue 17): one store, keyed by kind and identifier."""

    EMAIL = "email"
    PHONE = "phone"


class OtpVerification(StrEnum):
    """The outcome of checking a one-time code (Issue 17).

    - ``VERIFIED``: right code, in time, first use. The code is spent.
    - ``INVALID``: wrong code (one attempt used), or no code was ever issued.
    - ``EXPIRED``: the code's time is up, or it was already used.
    - ``LOCKED``: too many wrong attempts; even the right code is refused until a new one is issued.
    """

    VERIFIED = "verified"
    INVALID = "invalid"
    EXPIRED = "expired"
    LOCKED = "locked"


class DisplayMode(StrEnum):
    """What the waiting-room board may show about a ticket. Stored in ``sites.display_mode``.

    The server applies it before anything reaches the board (non-negotiable 4, Issue 58): under
    ``NUMBER_ONLY`` a name is not in the payload at all.

    - ``NUMBER_ONLY``: the ticket number and nothing else. The default for every new site.
    - ``NAME_LITE``: the number with a shortened name (first name and an initial).
    - ``FULL``: the number and the full display name.
    """

    NUMBER_ONLY = "number_only"
    NAME_LITE = "name_lite"
    FULL = "full"


#: The display mode every new site is created with, through every code path (Issue 27). Read this
#: constant; never restate the member at a call site.
SITE_DEFAULT_DISPLAY_MODE: DisplayMode = DisplayMode.NUMBER_ONLY

#: The display modes that put a patient's name on a public screen (Issue 27). Switching **to** one
#: of these needs clinic-manager permission, an explicit confirmation and an audit row; a screen
#: reads this set rather than naming the members, so a fourth mode cannot be added without deciding
#: which side of the line it falls on.
NAME_REVEALING_DISPLAY_MODES: frozenset[DisplayMode] = frozenset(
    {DisplayMode.NAME_LITE, DisplayMode.FULL}
)


class BoardLanguage(StrEnum):
    """The language a waiting-room board and its announcements use. ``sites.board_language``.

    South Africa has twelve official languages; these are the eleven written ones (South African
    Sign Language, the twelfth, has no written form for a board to render). A clinic picks the one
    its patients read, and the board, the audio announcements (Issue 60) and the channel menus all
    follow it.

    Values are ISO 639-1 codes where one exists, and ISO 639-3 where it does not (``nso``, ``tsn``,
    ``ven``, ``tso``, ``ssw``, ``nbl``), so a browser's ``lang`` attribute and a text-to-speech
    voice selection can use them unchanged.
    """

    ENGLISH = "en"
    AFRIKAANS = "af"
    ISIZULU = "zu"
    ISIXHOSA = "xh"
    ISINDEBELE = "nbl"
    SEPEDI = "nso"
    SESOTHO = "st"
    SETSWANA = "tsn"
    SISWATI = "ssw"
    TSHIVENDA = "ven"
    XITSONGA = "tso"


#: The board language a new site is created with. English, because it is the language every one of
#: the pilot clinics' signage already uses; a clinic changes it in one click.
SITE_DEFAULT_BOARD_LANGUAGE: BoardLanguage = BoardLanguage.ENGLISH


class TokenType(StrEnum):
    """Typed JWT ``type`` claim, plus the bearer scheme label.

    Every token this app signs carries a ``type``, and every decoder accepts exactly one. They all
    share ``JWT_SECRET``, so a decoder that did not check the type would take any of them: an
    unsubscribe link (long-lived, and sitting in a mailbox) presented as ``Authorization: Bearer``
    was a signed-in session until Issue 15 made the access token typed as well.
    """

    # The short-lived session token every authenticated request carries (Issue 15).
    ACCESS = "access"
    # A patient's web session after a phone OTP (Issue 17). Its own type, so a patient session is
    # never a staff session and a staff token never opens a patient's record.
    PATIENT_SESSION = "patient"
    ACTIVATION = "act"
    PASSWORD_RESET = "pwd_reset"
    BEARER = "bearer"
    # Typed, single-use link proving control of a *new* address before an email change takes
    # effect: the account keeps its old address until the link is confirmed (Issue #59).
    EMAIL_CHANGE = "email_change"
    # Short-lived, single-document download link so private tenant documents are never
    # served from a public URL (Issue #34).
    DOCUMENT_DOWNLOAD = "doc_dl"
    # Short-lived, single-attachment download link so private maintenance-request photos and
    # videos are never served from a public URL (Issue #47).
    MAINTENANCE_ATTACHMENT_DOWNLOAD = "maint_attach_dl"
    # Short-lived, single-statement download link so an owner statement (Issue #45) leaves the
    # owner portal only through a signed, expiring URL scoped to one owner and period (Issue #56).
    STATEMENT_DOWNLOAD = "stmt_dl"
    # Short-lived, single-photo download link so a private inspection photo (Issue #69) is never
    # served from a public URL.
    INSPECTION_PHOTO_DOWNLOAD = "insp_photo_dl"
    # Short-lived, single-document download link minted by the unified document storage service
    # (Issue #70): the only read path onto a stored document, scoped to one document id and the
    # actor it was minted for. Distinct from the per-module ``doc_dl`` so the two never cross.
    DOCUMENT_SERVICE_DOWNLOAD = "docsvc_dl"
    # Long-lived, login-free unsubscribe link for non-essential mail (Issue #72). Carries the
    # recipient email and the notification category it unsubscribes, signed so the endpoint can act
    # on it without a session and without a lookup that would reveal whether an account exists.
    UNSUBSCRIBE = "unsub"
    # A staff invitation link (Issue 22). Names one ``staff_invitation`` row and nothing else: the
    # role, the clinic and the expiry live on that row, so a link cannot claim a role it was not
    # issued for and accepting it once closes it for good.
    STAFF_INVITE = "staff_invite"


class AuthScope(StrEnum):
    """Scope for anonymous or minimal auth context (used when ``AUTH_ENABLED`` is off)."""

    READ = "read"


class PermissionVerb(StrEnum):
    """CRUD permission levels for a resource. Ordering is strict and cumulative.

    ``READ < CREATE < UPDATE < DELETE``: a granted verb implies every lower verb on
    the same resource (Issue #5).

    Since M25 (Issue #139–#142) the source of truth for the catalog is the DB ``actions`` table;
    this enum is a **typed convenience** whose values mirror the seeded CRUD verbs, validated to
    reproduce the catalog by ``tests/unit/security/test_rbac_catalog.py``. Non-cumulative named
    actions (``sign``/``approve``) live in :class:`PermissionAction`, not here.
    """

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class PermissionAction(StrEnum):
    """Named, **non-cumulative** RBAC actions beyond the CRUD verbs (Issue #140, M25).

    The cumulative ladder (``READ < CREATE < UPDATE < DELETE``) cannot express an action that is not
    "more than" another — you cannot model *signing* a lease or *approving* an application as a rung
    above ``delete``. ``.btk/RBAC/rbac-design.md`` §1 keeps actions in a catalog separate from
    resources so a permission is an explicit ``(resource, action)`` pair; these are the first such
    named actions, enforced directly (not via ``max_verb``). The value is the ``actions.key`` seeded
    in the DB catalog.

    - ``SIGN`` — execute a lease signature (``lease.signature``). Independent of ``delete``: a role
      may sign without being able to delete, and vice-versa.
    - ``APPROVE`` — approve a screening decision (``application.screening``) or an inspection
      deduction (``inspection.deductions`` — Issue #155: approving posts a real ``maintenance``
      charge to the tenant's active lease, the same "not just a rung above ``update``" shape as a
      signature, so it is no longer folded into the plain ``update`` verb every other deduction
      write shares).
    - ``REJECT`` — reject an inspection deduction (``inspection.deductions``, Issue #155). Posts
      nothing to the ledger (unlike ``approve``), but is the other half of the same "who may
      *decide*, not just *propose*" privilege split, so it gets the same named-action treatment for
      symmetry — a role may hold one without the other.
    - ``BROADCAST_GLOBAL`` — address an announcement to **every user in the system**
      (``communications.announcements``, Issue #160, M28). Not a rung above ``delete`` either:
      publishing an announcement to your own tenants and publishing one to the entire user base are
      different privileges, not different amounts of the same one. Until M28 this was the literal
      comparison ``sender.role != "admin"`` in ``src.modules.messaging.audience`` — an
      authorization decision with no grant behind it at all, which no permission could open for a
      custom role however broad. Seeded to ``admin`` only, so the default behaviour is identical.
    - ``TERMINATE`` — lodge a notice to terminate a lease (``lease.details``, Issue #167, M28). The
      tenant portal's "give notice" form writes a *pending* notice on the lease, so it is an
      ``update`` on ``lease.details`` in every mechanical sense — but granting a tenant that verb
      would hand them the lease **editor**, which is a different privilege entirely, not a smaller
      amount of the same one. Exactly the ``sign``/``approve`` shape, and exactly what the surface
      framework's §2 recipe prescribes for a control whose visibility must differ from the page's
      own CRUD verb. Confirming the notice stays on plain ``lease.details:UPDATE`` — deciding is
      the manager's act, giving notice is the tenant's.
    """

    SIGN = "sign"
    APPROVE = "approve"
    REJECT = "reject"
    BROADCAST_GLOBAL = "broadcast_global"
    TERMINATE = "terminate"


# The cumulative CRUD verb values, as a set — the discriminator between a cumulative grant and a
# named action. An ``action`` outside this set is a named, non-cumulative action (Issue #140).
CUMULATIVE_ACTION_KEYS: frozenset[str] = frozenset(
    verb.value for verb in PermissionVerb
)


def is_cumulative_action(action_key: str) -> bool:
    """Return True when ``action_key`` is a cumulative CRUD verb (vs. a named action)."""
    return action_key in CUMULATIVE_ACTION_KEYS


class PermissionEffect(StrEnum):
    """Whether a ``role_permission`` grant *adds* access or *subtracts* it (Issue #134).

    An ``ALLOW`` row grants its ``max_verb`` and every lower verb (the cumulative model of
    Issue #5). A ``DENY`` row **removes** its verb and every *higher* verb, and wins whenever
    an ALLOW and a DENY resolve for the same resource+verb — deny-beats-allow, including
    across parent->child inheritance and (from Issue #135) across the role closure. ``ALLOW``
    is the default so every pre-existing grant keeps its exact meaning.
    """

    ALLOW = "allow"
    DENY = "deny"


class GrantScope(StrEnum):
    """How wide a ``role_permission`` grant reaches — the *scope tier* condition (Issue #156, M28).

    RBAC's verb answers *what* a role may do; this answers *over whose rows it may do it*, as a
    property of the **grant** rather than of the role's name, so no role's name decides how wide
    it reaches, anywhere.

    In ClinicQ (Issue 18):

    - ``OWN`` — strictly the rows the caller is the *subject* of, resolved per resource shape by
      :func:`src.core.scope.resolve_scope`: a patient's own record; for a nurse, the queues they
      are personally assigned to. First-person only — an ``own`` grant never reaches a row the
      caller was merely *assigned to manage*.
    - ``ASSIGNED`` — ``own`` **plus** the rows reachable through the caller's explicit assignments:
      the sites a receptionist, nurse or clinic manager holds a role at
      (``UserRoleAssignment(scope_type='site')``) and the queues and tickets at those sites.
      Fail-closed: assigned to nothing reaches nothing.
    - ``BUSINESS`` — the whole platform: every row the resource has, the operator's view
      (``platform_admin``; reading a clinic it is not assigned to is audited, Issue 19).

    **Members are declared narrowest first, and that declaration order *is* the tier ladder**
    (Issue #165, M28): :attr:`tier_rank` and :meth:`satisfies` read it, so the middle tier
    Issue #171 inserted between them ordered itself everywhere by being declared in the right
    place — no comparison anywhere else in the codebase enumerates the tiers by name.

    The ladder is only meaningful because each rung is a **superset** of the one below it:
    ``own ⊆ assigned ⊆ business``. A tier added later must preserve that, or
    :meth:`satisfies` stops being a valid answer to "is this grant wide enough".
    """

    OWN = "own"
    ASSIGNED = "assigned"
    BUSINESS = "business"

    @property
    def tier_rank(self) -> int:
        """Return this tier's position on the ladder — ``0`` is the narrowest.

        Derived from member declaration order rather than a parallel table, so the ladder cannot
        drift from the enum it orders.
        """
        return list(type(self)).index(self)

    def satisfies(self, required: GrantScope) -> bool:
        """Return True when a grant at this tier is wide enough for a ``required``-tier surface.

        The ladder comparison every scope-aware gate funnels through (Issue #165): an ``own`` grant
        opens an ``own`` surface but not a ``business`` one, while a ``business`` grant opens both.
        Written as a rank comparison, never a pair of equality checks, so a tier added later orders
        itself.
        """
        return self.tier_rank >= required.tier_rank


class ImageContentType(StrEnum):
    """IANA media types accepted for image uploads.

    The upload endpoints' content-type allow-list: anything outside this set is a 415. Values are
    the exact ``Content-Type`` strings a client sends, so a router compares against these rather
    than against raw literals. Shared by every image surface (the account avatar, and whatever your
    modules upload), which is why it lives here rather than in one module's own enums.
    """

    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"


#: Pillow format name each accepted upload is re-encoded to on ingest. Every accepted upload is
#: decoded then re-saved as one of these, so the bytes served back are always freshly produced by
#: Pillow — never the client's original file. Keeping the output format aligned with the input type
#: avoids a lossy JPEG<->PNG round-trip while still guaranteeing a re-encode.
IMAGE_REENCODE_FORMAT: dict[ImageContentType, str] = {
    ImageContentType.JPEG: "JPEG",
    ImageContentType.PNG: "PNG",
    ImageContentType.WEBP: "WEBP",
}


class AssignmentScopeType(StrEnum):
    """What a scoped role assignment (``user_roles.scope_type``) is scoped to.

    ``NULL`` in the column is an unscoped assignment: the role applies everywhere. A value names the
    kind of thing ``scope_id`` points at.

    - ``INSTANCE``: the kernel's generic "these specific records" scope, written by the RBAC
      console.
    - ``SITE``: the clinic a staff member works at (Issue 15). A staff member's site is never a
      column on ``user``: it is a role held at a site, so one person can hold different roles at
      two clinics, and every site-scoped query resolves "which sites" from these rows (Issue 19).
    - ``QUEUE``: the queue a nurse or doctor was put on (Issue 19, filled in by Issue 28). Narrower
      than a site: it is what makes a nurse's ``own``-tier call-next grant reach their own room's
      queue and no other.
    """

    INSTANCE = "instance"
    SITE = "site"
    QUEUE = "queue"
    CLINIC_SERVICE = "clinic_service"


class ScopeShape(StrEnum):
    """How a resource expresses "the caller's own rows" (Issue #156, M28).

    The companion to :class:`GrantScope`: the tier says *how wide* a grant reaches, this says *what
    narrowing to apply* when that tier is ``own``, because "your own" means a different column per
    resource shape. Declared per resource by its owning module's manifest
    (``ResourceSpec.scope_shape``, inherited by descendants exactly as ``actions`` is) and resolved
    by :func:`src.core.scope.resolve_scope`, which turns it into the concrete id set.
    """

    #: Rows hang off a scoped instance — the ids a caller's ``user_roles`` assignments name,
    #: plus whatever :func:`src.core.scope.own_instance_ids` resolves as first-person. Narrow by
    #: ``ScopeNarrowing.instance_ids``. The default for any resource declaring no shape.
    INSTANCE_DERIVED = "instance_derived"
    #: Rows are shared conversations — narrow to the threads ``user_id`` participates in.
    THREAD_PARTICIPANT = "thread_participant"
    #: The row **is** a clinic, or carries its ``site_id``: narrow by
    #: :attr:`~src.core.scope.ScopeNarrowing.site_ids`, the sites the caller holds a role at
    #: (Issue 19). Tickets and everything else hanging off a clinic share this shape.
    SITE = "site"
    #: The row is a queue, or carries its ``queue_id``: at ``own`` narrow by
    #: :attr:`~src.core.scope.ScopeNarrowing.queue_ids` (the queues the caller is assigned to), at
    #: ``assigned`` by the sites those queues belong to (Issue 19).
    QUEUE = "queue"

    # Your own shapes go here — one per "whose rows are these" question your domain asks, e.g. a
    # row carrying a customer identity or an assigned technician. Add the member, declare it on the
    # owning module's ``ResourceSpec.scope_shape``, and resolve it in
    # :func:`src.core.scope.resolve_scope`.


class PermissionAuditAction(StrEnum):
    """The kind of RBAC-admin mutation recorded in ``permission_audit_log`` (Issue #138).

    ``GRANT`` covers creating or widening access (a matrix cell set, an inheritance edge or
    assignment added); ``REVOKE`` covers removing or narrowing it. Role/hierarchy edits map onto
    these two so the trail reads uniformly.
    """

    GRANT = "grant"
    REVOKE = "revoke"


class PermissionAuditTargetType(StrEnum):
    """What kind of grant or catalog row an audit row is about (Issue #138, #141)."""

    ROLE_PERMISSION = "role_permission"
    ROLE_HIERARCHY = "role_hierarchy"
    USER_ROLE = "user_role"
    # Catalog mutations from the dynamic admin UI (Issue #141): a resource/action/permission
    # created, edited or deleted. GRANT covers create/edit (widen), REVOKE covers delete (remove).
    RESOURCE = "resource"
    ACTION = "action"
    PERMISSION = "permission"
    # A nav/tab surface re-gated to a different resource/verb or named action, or reset back to its
    # Python-declared default (Issue #146). GRANT covers a re-gate, REVOKE covers a reset-to-default.
    NAV_GATE_OVERRIDE = "nav_gate_override"


# RBAC resource keys used to live here, as a ``PermissionResource`` ``StrEnum`` plus a static
# ``PERMISSION_RESOURCE_PARENT`` parent map. Both were permanently deleted in Issue #154 (M27):
# they were a shared, hand-maintained chokepoint every new module had to edit, and the catalog's
# source of truth is now the DB ``resources`` table whose shape each module declares in its own
# ``src/modules/<name>/rbac_manifest.py`` (see ``docs/architecture/rbac-module-self-registration.md``
# §7 and ``src.core.rbac_manifest_registry``). Resources are **plain strings**, permanently — do not
# reintroduce an enum, a generated-constants module or any other successor for them.
# ``tests/unit/security/test_rbac_enum_retired.py`` is a standing CI guard that fails the build if
# one reappears here or anywhere else under ``src/``, under any name.


# --------------------------------------------------------------------------------------
# Notifications (Issue #67) — one service for transactional email + SMS with delivery status.
# --------------------------------------------------------------------------------------


class NotificationChannel(StrEnum):
    """Delivery channel for a notification. Stored in ``notification.channel``."""

    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(StrEnum):
    """Lifecycle of one notification. Stored in ``notification.status``.

    ``QUEUED`` — recorded but not yet handed to a provider (the retry sweep will pick it up).
    ``SENT`` — accepted by the provider (email handed to SMTP, SMS accepted by the gateway).
    ``DELIVERED`` — the provider confirmed delivery via a status webhook (email/SMS that
    support it); a terminal, better-than-``SENT`` state.
    ``FAILED`` — the last attempt failed transiently and the row is awaiting another try.
    ``DEAD`` — retries are exhausted (``attempts >= max_attempts``); the message is
    dead-lettered, never lost silently, so an operator can see it stopped.
    ``SUPPRESSED`` — no transport was configured (development without SMTP/SMS), so nothing
    was sent; recorded only when a ledger row already exists.
    """

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"
    SUPPRESSED = "suppressed"


class NotificationTemplate(StrEnum):
    """Template key identifying which transactional message a notification carries.

    Keeping the key on the row (rather than only the rendered body) means delivery status is
    queryable *per template* and a provider webhook can be traced back to what was sent. New
    flows add a key here and register a renderer in
    :mod:`src.modules.notifications.templates`.
    """

    # Account & auth (M1 / M9)
    ACCOUNT_ACTIVATION = "account_activation"
    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE_VERIFICATION = "email_change_verification"
    OTP_SIGN_IN = "otp_sign_in"
    STAFF_INVITATION = "staff_invitation"
    # Applications & screening (M5)
    APPLICATION_ACKNOWLEDGEMENT = "application_acknowledgement"
    APPLICATION_IN_SCREENING = "application_in_screening"
    APPLICATION_ACCEPTED = "application_accepted"
    APPLICATION_REJECTED = "application_rejected"
    APPLICATION_WITHDRAWN = "application_withdrawn"
    # Leases (M6)
    LEASE_EXPIRY_REMINDER = "lease_expiry_reminder"
    # Tenant-initiated termination (M18 — Issue #107): the manager is notified when a tenant lodges
    # a notice to terminate, and the tenant is notified when the manager confirms it.
    LEASE_TERMINATION_REQUESTED = "lease_termination_requested"
    LEASE_TERMINATION_CONFIRMED = "lease_termination_confirmed"
    # Maintenance (M8)
    MAINTENANCE_REQUEST_RECEIVED = "maintenance_request_received"
    MAINTENANCE_NEW_REQUEST = "maintenance_new_request"
    MAINTENANCE_WORK_SCHEDULED = "maintenance_work_scheduled"
    MAINTENANCE_COMPLETED = "maintenance_completed"
    MAINTENANCE_APPROVAL_REQUIRED = "maintenance_approval_required"
    WORK_ORDER_VENDOR_ASSIGNMENT = "work_order_vendor_assignment"
    MAINTENANCE_MANAGER_DIGEST = "maintenance_manager_digest"
    # In-app messaging (M11 / Issue #68) — a new message on a thread notifies its other
    # participants; its own key keeps that delivery status queryable apart from other mail.
    NEW_MESSAGE = "new_message"
    # Catch-all for a pre-rendered message with no dedicated key (kept small on purpose).
    GENERIC = "generic"


class SmsProviderKind(StrEnum):
    """Which SMS provider implementation backs the SMS channel (env: ``SMS_PROVIDER``).

    ``LOGGING`` is the default: it logs the message and returns a synthetic id, so the app
    runs end-to-end without an SMS account (the email-``SMTP_HOST``-unset analogue). ``FAKE``
    is the in-memory test double asserted against in the suite. Real gateways are added here
    as they are integrated behind :class:`~src.modules.notifications.sms.SmsProvider`.
    """

    LOGGING = "logging"
    FAKE = "fake"


# --------------------------------------------------------------------------------------
# Notification preferences (Issue #72) — per-user control over which categories reach them,
# on which channel, plus quiet hours and login-free unsubscribe. Once SMS and messaging exist
# the product can become noisy enough that people mute it entirely; preferences keep the
# essential messages deliverable while giving users control over the rest.
# --------------------------------------------------------------------------------------


class NotificationCategory(StrEnum):
    """The category a notification belongs to — the unit a user sets a preference on.

    Every :class:`NotificationTemplate` maps to exactly one category
    (:data:`NOTIFICATION_TEMPLATE_CATEGORY`). *Essential* categories
    (:data:`NOTIFICATION_ESSENTIAL_CATEGORIES`) cannot be fully switched off — a user may change
    the channel they arrive on, but the delivery itself is guaranteed on at least one channel — so
    an account can never mute the security and money messages that it must receive.

    - ``ACCOUNT`` (essential): account & security mail — activation, password reset, sign-in
      codes, email-change verification, and any un-categorised transactional message.
    - ``FINANCIAL`` (essential): money and lease-continuity notices (e.g. lease expiry).
    - ``APPLICATIONS``: rental-application status updates.
    - ``MAINTENANCE``: maintenance requests, work orders and their status.
    - ``MESSAGES``: a new in-app message on a thread (Issue #68).
    - ``MARKETING``: promotional and non-transactional mail; always opt-outable.
    """

    ACCOUNT = "account"
    FINANCIAL = "financial"
    APPLICATIONS = "applications"
    MAINTENANCE = "maintenance"
    MESSAGES = "messages"
    MARKETING = "marketing"


class NotificationChannelPreference(StrEnum):
    """A user's chosen delivery channel for one category.

    ``EMAIL`` / ``SMS`` select the channel; ``OFF`` opts out entirely (permitted only for
    non-essential categories — an essential category set to ``OFF`` is coerced back to ``EMAIL``,
    the always-available fallback, so the message is still delivered).
    """

    EMAIL = "email"
    SMS = "sms"
    OFF = "off"


# Which category each template belongs to. The single source of truth the preference layer reads;
# a new template must be added here so its delivery is governed by a category (a missing key falls
# back to the essential ``ACCOUNT`` category, i.e. it is always delivered — fail-safe, never
# silently dropped).
NOTIFICATION_TEMPLATE_CATEGORY: dict[NotificationTemplate, NotificationCategory] = {
    # Account & auth (essential)
    NotificationTemplate.ACCOUNT_ACTIVATION: NotificationCategory.ACCOUNT,
    NotificationTemplate.PASSWORD_RESET: NotificationCategory.ACCOUNT,
    NotificationTemplate.EMAIL_CHANGE_VERIFICATION: NotificationCategory.ACCOUNT,
    NotificationTemplate.OTP_SIGN_IN: NotificationCategory.ACCOUNT,
    NotificationTemplate.STAFF_INVITATION: NotificationCategory.ACCOUNT,
    NotificationTemplate.GENERIC: NotificationCategory.ACCOUNT,
    # Applications & screening
    NotificationTemplate.APPLICATION_ACKNOWLEDGEMENT: NotificationCategory.APPLICATIONS,
    NotificationTemplate.APPLICATION_IN_SCREENING: NotificationCategory.APPLICATIONS,
    NotificationTemplate.APPLICATION_ACCEPTED: NotificationCategory.APPLICATIONS,
    NotificationTemplate.APPLICATION_REJECTED: NotificationCategory.APPLICATIONS,
    NotificationTemplate.APPLICATION_WITHDRAWN: NotificationCategory.APPLICATIONS,
    # Leases — expiry and termination are lease-continuity / financial notices (essential)
    NotificationTemplate.LEASE_EXPIRY_REMINDER: NotificationCategory.FINANCIAL,
    NotificationTemplate.LEASE_TERMINATION_REQUESTED: NotificationCategory.FINANCIAL,
    NotificationTemplate.LEASE_TERMINATION_CONFIRMED: NotificationCategory.FINANCIAL,
    # Maintenance & work orders
    NotificationTemplate.MAINTENANCE_REQUEST_RECEIVED: NotificationCategory.MAINTENANCE,
    NotificationTemplate.MAINTENANCE_NEW_REQUEST: NotificationCategory.MAINTENANCE,
    NotificationTemplate.MAINTENANCE_WORK_SCHEDULED: NotificationCategory.MAINTENANCE,
    NotificationTemplate.MAINTENANCE_COMPLETED: NotificationCategory.MAINTENANCE,
    NotificationTemplate.MAINTENANCE_APPROVAL_REQUIRED: NotificationCategory.MAINTENANCE,
    NotificationTemplate.WORK_ORDER_VENDOR_ASSIGNMENT: NotificationCategory.MAINTENANCE,
    NotificationTemplate.MAINTENANCE_MANAGER_DIGEST: NotificationCategory.MAINTENANCE,
    # In-app messaging (Issue #68)
    NotificationTemplate.NEW_MESSAGE: NotificationCategory.MESSAGES,
}


# Categories a user may never fully disable: the delivery is guaranteed (the channel may change,
# the message is not dropped). The single place "essential" is defined.
NOTIFICATION_ESSENTIAL_CATEGORIES: frozenset[NotificationCategory] = frozenset(
    {NotificationCategory.ACCOUNT, NotificationCategory.FINANCIAL}
)


# Categories surfaced in the in-app notification centre (Issue #113): the domain-event feed the
# shell bell previews. ACCOUNT (auth/security codes and links) is deliberately excluded — the bell
# is not the place for a sign-in code — as is MARKETING (promotional), while MESSAGES is surfaced
# via the messaging thread/unread machinery (Issue #112), not duplicated as a centre item. The
# single source of truth for "does this event belong in the centre".
NOTIFICATION_CENTER_CATEGORIES: frozenset[NotificationCategory] = frozenset(
    {
        NotificationCategory.APPLICATIONS,
        NotificationCategory.MAINTENANCE,
        NotificationCategory.FINANCIAL,
    }
)


# Templates whose delivery is time-critical and therefore exempt from quiet-hours deferral — the
# recipient is actively waiting on them (a sign-in code, a reset link). Everything else is deferred
# rather than dropped when it lands inside a user's quiet hours.
NOTIFICATION_URGENT_TEMPLATES: frozenset[NotificationTemplate] = frozenset(
    {
        NotificationTemplate.ACCOUNT_ACTIVATION,
        NotificationTemplate.PASSWORD_RESET,
        NotificationTemplate.EMAIL_CHANGE_VERIFICATION,
        NotificationTemplate.OTP_SIGN_IN,
        NotificationTemplate.STAFF_INVITATION,
    }
)

#: Templates whose message *is* a secret, and the payload fields that carry it (Issue 17). The
#: notification ledger keeps a row for each send, but never these fields' values: the row stores a
#: placeholder, the message is rendered from the real value in memory and handed to the transport
#: once, and it is never retried (a retry would render the placeholder, and a late code is no use).
NOTIFICATION_SECRET_FIELDS: dict[NotificationTemplate, frozenset[str]] = {
    NotificationTemplate.OTP_SIGN_IN: frozenset({"code"}),
    # An invitation link is a credential: whoever opens it sets the password for that account
    # (Issue 22). The ledger keeps the row, never the link.
    NotificationTemplate.STAFF_INVITATION: frozenset({"link"}),
}


def notification_category_for(template: NotificationTemplate) -> NotificationCategory:
    """Return the category governing ``template`` (essential ``ACCOUNT`` for any unmapped key)."""
    return NOTIFICATION_TEMPLATE_CATEGORY.get(template, NotificationCategory.ACCOUNT)


def is_essential_category(category: NotificationCategory) -> bool:
    """Whether ``category`` can never be fully disabled (delivery guaranteed on ≥1 channel)."""
    return category in NOTIFICATION_ESSENTIAL_CATEGORIES


def is_urgent_template(template: NotificationTemplate) -> bool:
    """Whether ``template`` is time-critical and so exempt from quiet-hours deferral."""
    return template in NOTIFICATION_URGENT_TEMPLATES


def is_center_category(category: NotificationCategory) -> bool:
    """Whether ``category`` events are surfaced in the in-app notification centre (Issue #113)."""
    return category in NOTIFICATION_CENTER_CATEGORIES


class DocumentScannerKind(StrEnum):
    """Which virus scanner backs document ingest (env: ``DOCUMENT_SCANNER``) — Issue #70.

    Scanning runs on the raw bytes before a document row is committed, so an infected upload
    is refused at the door rather than stored and served later.

    - ``EICAR`` is the default: a minimal but *real* signature scanner that flags the
      industry-standard `EICAR test file <https://www.eicar.org/download-anti-malware-testfile/>`_
      and passes everything else. It needs no external daemon, so the app runs end-to-end (the
      ``SMTP_HOST``-unset analogue), and the standard test signature makes the ingest guard
      verifiable without shipping a live sample of malware.
    - ``FAKE`` is the in-memory test double whose verdict the suite pins per case.

    A real engine (e.g. ClamAV over its socket) is added here as it is integrated behind
    :class:`~src.modules.documents.virus_scan.DocumentScanner`.
    """

    EICAR = "eicar"
    FAKE = "fake"


# --------------------------------------------------------------------------------------
# E-signature integration (Issue #71) — a lease/addendum is sent for signature through a
# pluggable provider, its envelope status tracked, and the executed document plus its
# certificate of completion retained on the unified document store.
# --------------------------------------------------------------------------------------


class EsignProviderKind(StrEnum):
    """Which e-signature provider backs the signing flow (env: ``ESIGN_PROVIDER``) — Issue #71.

    The lease is the most legally sensitive artefact in the system, so the provider sits behind
    :class:`~src.modules.documents.esign.EsignProvider` and never leaks into the domain:

    - ``LOCAL`` is the default: a self-contained provider that creates an envelope, returns a
      synthetic id and can produce a signed document plus a completion certificate without any
      external account, so the whole flow runs end-to-end in development and CI (the ``SMTP_HOST``
      -unset / EICAR analogue). It signs a webhook with the configured secret exactly as a real
      gateway would, so the verification and idempotency paths are exercised for real.
    - ``FAKE`` is the in-memory test double the suite asserts against and drives on demand.

    A real gateway (DocuSign, Dropbox Sign, …) is added here as it is integrated behind the same
    interface and selected by settings, without touching the service.
    """

    LOCAL = "local"
    FAKE = "fake"


class EsignEnvelopeStatus(StrEnum):
    """Lifecycle of one signature envelope. Stored in ``esign_envelope.status`` — Issue #71.

    Written only through :mod:`src.modules.documents.esign_service`. The three *terminal* outcomes
    (``SIGNED`` / ``DECLINED`` / ``EXPIRED``) are applied idempotently from a verified provider
    webhook — a replayed callback never re-runs their side effects — and ``VOIDED`` is a manager
    cancelling an in-flight envelope.

    - ``CREATED``: the envelope exists but has not yet been handed to the provider.
    - ``SENT``: handed to the provider and awaiting the recipient's signature.
    - ``SIGNED``: fully executed — the signed document and completion certificate are retained and
      the lease may be activated (terminal, the success state).
    - ``DECLINED``: the recipient refused to sign (terminal).
    - ``EXPIRED``: the signing window lapsed before completion (terminal).
    - ``VOIDED``: cancelled before completion by a manager (terminal).
    """

    CREATED = "created"
    SENT = "sent"
    SIGNED = "signed"
    DECLINED = "declined"
    EXPIRED = "expired"
    VOIDED = "voided"


class EsignWebhookEvent(StrEnum):
    """The provider webhook event types the service acts on. Values are the wire strings.

    Only the *terminal* events are handled — a ``completed`` callback executes the envelope
    (stores the signed document and certificate), a ``declined`` or ``expired`` callback closes
    it. Intermediate provider events (delivered, viewed, …) are accepted and ignored, so an
    unrecognised event is never an error.
    """

    COMPLETED = "completed"
    DECLINED = "declined"
    EXPIRED = "expired"


# Provider webhook event -> the terminal envelope status it drives the envelope to. Defined once
# here so the webhook handler never re-encodes the mapping as inline checks.
ESIGN_WEBHOOK_EVENT_STATUS: dict[EsignWebhookEvent, EsignEnvelopeStatus] = {
    EsignWebhookEvent.COMPLETED: EsignEnvelopeStatus.SIGNED,
    EsignWebhookEvent.DECLINED: EsignEnvelopeStatus.DECLINED,
    EsignWebhookEvent.EXPIRED: EsignEnvelopeStatus.EXPIRED,
}

# Envelope statuses in which no further transition is possible — a webhook for one of these is a
# no-op replay. The single source of truth the service consults so "terminal" is defined once.
ESIGN_TERMINAL_STATUSES: frozenset[EsignEnvelopeStatus] = frozenset(
    {
        EsignEnvelopeStatus.SIGNED,
        EsignEnvelopeStatus.DECLINED,
        EsignEnvelopeStatus.EXPIRED,
        EsignEnvelopeStatus.VOIDED,
    }
)


# --------------------------------------------------------------------------------------
# Record-level audit trail (Issue #78) — a persisted, append-only audit event per mutation
# of a sensitive record, distinct from the request-level SECURITY_AUDIT log lines above.
# --------------------------------------------------------------------------------------


class AuditAction(StrEnum):
    """The kind of change an :class:`~src.database.models.audit_event.AuditEvent` records.

    ``CREATE`` / ``UPDATE`` / ``DELETE`` are the mutations of a sensitive record; ``READ`` is
    written when a privileged actor searches or exports the audit trail itself (so reading the
    log is itself audited); ``EXPORT`` and ``ERASE`` are the POPIA data-subject operations.
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ = "read"
    EXPORT = "export"
    ERASE = "erase"


class AuditEntityType(StrEnum):
    """The type of record an audit event is about.

    A small, **closed** set: one member per kind of record whose changes are worth keeping a
    permanent trail of. Add a member when you add such a record — the column is a string, so a
    missing member fails at the call site rather than silently writing an unqueryable value.

    ``AUDIT_LOG`` and ``DATA_SUBJECT`` cover the meta-events: written when the trail is itself
    read, or when a data subject is exported or erased.
    """

    USER = "user"
    PATIENT = "patient"
    PATIENT_CONSENT = "patient_consent"
    SITE = "site"
    QUEUE = "queue"
    TICKET = "ticket"
    CLINIC_SERVICE = "clinic_service"
    STAFF_INVITATION = "staff_invitation"
    AUDIT_LOG = "audit_log"
    DATA_SUBJECT = "data_subject"
    DOCUMENT = "document"
    WIDGET = "widget"


# Field names whose values must never be written into an audit diff in the clear: encrypted or
# otherwise sensitive personal data. The diff records that such a field *changed* (and nothing
# more) so the trail stays useful without becoming a second plaintext copy of the secret.
AUDIT_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        # Credentials: the trail records *that* one changed, never its value.
        "password",
        "hashed_password",
        "totp_secret",
        "token_hash",
        # Personal data (Issue 20): the audit exists to show *what changed*, not to become a
        # second copy of a patient's record. The field name and the fact of the change are kept;
        # the values are not. Anything a data subject could be identified or contacted by belongs
        # here — add to this set when you add such a column.
        "phone_e164",
        "whatsapp_id",
        "display_name",
        "walk_in_name",
        "email",
        "first_name",
        "last_name",
        "date_of_birth",
        "id_number",
        "avatar_url",
        "reason_text",
        "note_text",
        "sign_in_ip",
        "user_agent",
    }
)
