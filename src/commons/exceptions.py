"""Application-wide exception types."""


class BKPropertyError(Exception):
    """Base error for domain and infrastructure failures."""

    def __init__(self, message: str, *, code: str = "properties.error") -> None:
        super().__init__(message)
        self.code = code


class UnitStatusTransitionError(BKPropertyError):
    """Raised when a requested unit status change is not a legal transition (Issue #23).

    Carries the ``current`` and ``target`` statuses so the API layer can build a clear
    message and map it to HTTP 409 Conflict. Stored as plain strings to keep this
    commons module free of a dependency on the properties enum.
    """

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Illegal unit status transition: {current} -> {target}.",
            code="properties.unit.illegal_status_transition",
        )
        self.current = current
        self.target = target


class ApplicationStatusTransitionError(BKPropertyError):
    """Raised when a requested application status change is not legal (Issue #29).

    Carries the ``current`` and ``target`` statuses so the API layer can build a clear
    message and map it to HTTP 409 Conflict, leaving the record untouched. Stored as plain
    strings to keep this commons module free of a dependency on the applications enum.
    """

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Illegal application status transition: {current} -> {target}.",
            code="applications.illegal_status_transition",
        )
        self.current = current
        self.target = target


class InvalidImageError(BKPropertyError):
    """Raised when an uploaded unit photo cannot be decoded as a real image (Issue #24).

    The content-type allow-list is checked before this, so reaching here means the bytes
    claimed an accepted image type but Pillow could not open/re-encode them. The API layer
    maps it to HTTP 422 Unprocessable Content.
    """

    def __init__(self, message: str = "Uploaded file is not a valid image.") -> None:
        super().__init__(message, code="properties.photo.invalid_image")


class ApplicationUnitNotFoundError(BKPropertyError):
    """Raised when a public application targets a unit that does not exist (Issue #28).

    A missing (or soft-deleted) unit is indistinguishable from one that was never listed,
    so the API layer maps this to HTTP 404 Not Found rather than leaking existence.
    """

    def __init__(self, unit_id: str) -> None:
        super().__init__(
            f"No such unit: {unit_id}.",
            code="applications.unit.not_found",
        )
        self.unit_id = unit_id


class UnitNotAvailableError(BKPropertyError):
    """Raised when an application targets a unit that is not ``available`` (Issue #28).

    Applications are only accepted for units on the market; any other status (pending,
    occupied, maintenance, off-market) is rejected. Carries the unit's current status so
    the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, unit_id: str, current_status: str) -> None:
        super().__init__(
            f"Unit {unit_id} is not available for applications (status: {current_status}).",
            code="applications.unit.not_available",
        )
        self.unit_id = unit_id
        self.current_status = current_status


class DuplicateApplicationError(BKPropertyError):
    """Raised when an applicant already holds an open application for the unit (Issue #28).

    The database enforces at most one *open* application per (unit, applicant email) via a
    partial unique index; hitting it surfaces here so the API layer can return HTTP 409
    Conflict instead of a raw integrity error.
    """

    def __init__(self, unit_id: str, applicant_email: str) -> None:
        super().__init__(
            f"An open application already exists for {applicant_email} on unit {unit_id}.",
            code="applications.duplicate_open",
        )
        self.unit_id = unit_id
        self.applicant_email = applicant_email


class TenantNotFoundError(BKPropertyError):
    """Raised when a tenant profile cannot be found by id (Issue #33).

    A missing (or soft-deleted) tenant is indistinguishable from one that never existed, so
    the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, tenant_id: str) -> None:
        super().__init__(
            f"No such tenant: {tenant_id}.",
            code="tenants.not_found",
        )
        self.tenant_id = tenant_id


class DuplicateTenantError(BKPropertyError):
    """Raised when a tenant with the same email already exists (Issue #33).

    A live tenant per email keeps the directory to one row per person; hitting the partial
    unique index (or the pre-check) surfaces here so the API layer returns HTTP 409 Conflict
    instead of a raw integrity error.
    """

    def __init__(self, email: str) -> None:
        super().__init__(
            f"A tenant already exists with email {email}.",
            code="tenants.duplicate_email",
        )
        self.email = email


class ApplicationNotAcceptedError(BKPropertyError):
    """Raised when a tenant is created from an application that is not ``accepted`` (Issue #33).

    Only an accepted application may be converted into a tenant. Carries the application's
    current status so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, application_id: str, current_status: str) -> None:
        super().__init__(
            f"Application {application_id} is not accepted (status: {current_status}); "
            "only an accepted application can be converted into a tenant.",
            code="tenants.application_not_accepted",
        )
        self.application_id = application_id
        self.current_status = current_status


class TenantUserLinkConflictError(BKPropertyError):
    """Raised when linking a ``User`` to a tenant conflicts with an existing link (Issue #33).

    The link is one-to-one and idempotent: re-linking the *same* user is a no-op, but linking
    a user already bound to another tenant — or a tenant already bound to a different user — is
    a conflict the API layer maps to HTTP 409 Conflict.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="tenants.user_link_conflict")


class TenancyNotFoundError(BKPropertyError):
    """Raised when a tenancy cannot be found by id (Issue #35).

    A missing (or soft-deleted) tenancy is indistinguishable from one that never existed, so
    the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, tenancy_id: str) -> None:
        super().__init__(
            f"No such tenancy: {tenancy_id}.",
            code="tenancies.not_found",
        )
        self.tenancy_id = tenancy_id


class UnitNotReadyForTenancyError(BKPropertyError):
    """Raised when a tenancy is created for a unit that is neither available nor held (Issue #35).

    A tenancy may only be opened against a unit that is on the market (``available``) or already
    held (``pending``) for this move-in; a unit that is ``occupied``, ``maintenance`` or
    ``off_market`` is rejected. Carries the unit's current status so the API layer can build a
    clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, unit_id: str, current_status: str) -> None:
        super().__init__(
            f"Unit {unit_id} is not ready for a new tenancy (status: {current_status}).",
            code="tenancies.unit_not_ready",
        )
        self.unit_id = unit_id
        self.current_status = current_status


class TenancyStateError(BKPropertyError):
    """Raised when a move-in / move-out is attempted from the wrong tenancy state (Issue #35).

    Move-in requires a ``pending`` tenancy and move-out an ``active`` one; anything else
    (moving in twice, moving out before moving in, acting on an ``ended`` tenancy) is rejected
    with the record left untouched. Carries the tenancy's current status and the attempted
    action so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, tenancy_id: str, current_status: str, action: str) -> None:
        super().__init__(
            f"Tenancy {tenancy_id} cannot {action} from status {current_status}.",
            code="tenancies.illegal_state",
        )
        self.tenancy_id = tenancy_id
        self.current_status = current_status
        self.action = action


class OutstandingBalanceError(BKPropertyError):
    """Raised when a move-out is blocked by an unpaid balance and no override (Issue #35).

    A tenancy carrying a positive outstanding balance may not be closed unless the caller
    explicitly overrides the block with a reason (which is then recorded). Carries the balance
    (in minor units) so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, tenancy_id: str, balance_minor: int) -> None:
        super().__init__(
            f"Tenancy {tenancy_id} has an outstanding balance of {balance_minor} "
            "(minor units); move-out requires an explicit override with a reason.",
            code="tenancies.outstanding_balance",
        )
        self.tenancy_id = tenancy_id
        self.balance_minor = balance_minor


class LeaseNotFoundError(BKPropertyError):
    """Raised when a lease cannot be found by id (Issue #36).

    A missing (or soft-deleted) lease is indistinguishable from one that never existed, so the
    API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, lease_id: str) -> None:
        super().__init__(
            f"No such lease: {lease_id}.",
            code="leases.not_found",
        )
        self.lease_id = lease_id


class LeaseNotEditableError(BKPropertyError):
    """Raised when a term change or delete is attempted on a non-draft lease (Issue #36).

    Once a lease is ``active`` it is effectively a signed contract, so its terms are immutable
    and it may not be deleted; an ``expired`` / ``terminated`` lease is likewise a closed record
    of what was agreed. Only a ``draft`` may be edited or deleted. Carries the lease's current
    status so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, lease_id: str, current_status: str) -> None:
        super().__init__(
            f"Lease {lease_id} is not editable in status {current_status}; only a draft "
            "lease can be changed or deleted.",
            code="leases.not_editable",
        )
        self.lease_id = lease_id
        self.current_status = current_status


class LeaseStateError(BKPropertyError):
    """Raised when a lease lifecycle transition is attempted from the wrong state (Issue #36).

    Activation requires a ``draft`` lease and termination an ``active`` one; anything else
    (activating twice, terminating a draft, acting on a closed lease) is rejected with the
    record left untouched. Carries the lease's current status and the attempted action so the
    API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, lease_id: str, current_status: str, action: str) -> None:
        super().__init__(
            f"Lease {lease_id} cannot {action} from status {current_status}.",
            code="leases.illegal_state",
        )
        self.lease_id = lease_id
        self.current_status = current_status
        self.action = action


class LeaseOverlapError(BKPropertyError):
    """Raised when activating a lease would overlap another active lease on the unit (Issue #36).

    Two active leases can never overlap in time on the same unit — the database enforces it with
    an exclude constraint, and the service pre-checks it for a clean error. Carries the unit and
    the offending term so the API layer can build a clear message and map it to HTTP 409
    Conflict.
    """

    def __init__(self, unit_id: str, start_date: object, end_date: object) -> None:
        super().__init__(
            f"An active lease already overlaps {start_date}..{end_date} on unit {unit_id}.",
            code="leases.overlap",
        )
        self.unit_id = unit_id
        self.start_date = start_date
        self.end_date = end_date


class LeaseTerminationRequestError(BKPropertyError):
    """Raised when a tenant-initiated termination request breaks a guard (Issue #107).

    Covers the workflow guards around a notice to terminate: a second request while one is already
    pending, confirming/rejecting when there is no pending request, or a confirmation date that
    would precede the notice window. Carries a specific ``code`` per case so the API layer can map
    it to HTTP 409 Conflict with a clear message, the record left untouched.
    """

    def __init__(
        self, message: str, *, code: str = "leases.termination_request"
    ) -> None:
        super().__init__(message, code=code)


class InvalidLeaseTermError(BKPropertyError):
    """Raised when a lease term is not a positive interval (Issue #36).

    The end date must strictly follow the start date; a zero-length or inverted term is
    rejected. The database also guards this with a CHECK constraint, but the service raises this
    for a clean message the API layer maps to HTTP 422 Unprocessable Content.
    """

    def __init__(self, start_date: object, end_date: object) -> None:
        super().__init__(
            f"Lease end date ({end_date}) must be after the start date ({start_date}).",
            code="leases.invalid_term",
        )
        self.start_date = start_date
        self.end_date = end_date


class InvalidRenewalTermError(BKPropertyError):
    """Raised when a renewal's successor term does not follow its predecessor (Issue #39).

    A renewal succeeds a lease rather than mutating it, so the successor's term must begin **on
    or after** the predecessor's end date — a successor that starts before the predecessor ends
    would overlap the very lease it renews. Carries the predecessor's end date and the proposed
    successor start so the API layer can build a clear message and map it to HTTP 422
    Unprocessable Content.
    """

    def __init__(self, predecessor_end: object, successor_start: object) -> None:
        super().__init__(
            f"Renewal must start on or after the predecessor's end date "
            f"({predecessor_end}); got {successor_start}.",
            code="leases.invalid_renewal_term",
        )
        self.predecessor_end = predecessor_end
        self.successor_start = successor_start


class LeaseTemplateNotFoundError(BKPropertyError):
    """Raised when a lease template cannot be found by name (or version) (Issue #37).

    A missing (or soft-deleted) template is indistinguishable from one that never existed, so
    the API layer maps this to HTTP 404 Not Found. Carries the name (and version, when a
    specific one was requested) for a clear message.
    """

    def __init__(self, name: str, version: int | None = None) -> None:
        target = f"{name!r}" if version is None else f"{name!r} version {version}"
        super().__init__(
            f"No such lease template: {target}.",
            code="leases.template_not_found",
        )
        self.name = name
        self.version = version


class DuplicateLeaseTemplateError(BKPropertyError):
    """Raised when creating a lease template whose name already exists (Issue #37).

    A template name is created once; every later change is an *edit* that publishes a new
    version rather than a second create. Carries the name so the API layer can build a clear
    message and map it to HTTP 409 Conflict (directing the caller to edit instead).
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"A lease template named {name!r} already exists; edit it to add a new version.",
            code="leases.template_duplicate",
        )
        self.name = name


class UnknownPlaceholderError(BKPropertyError):
    """Raised when a template uses a ``{{ token }}`` outside the known vocabulary (Issue #37).

    Placeholders are a closed set (:class:`~src.modules.leases.enums.LeasePlaceholder`); an
    unrecognised token can never be filled, so it is rejected loudly — when the template is
    authored and again at render time — rather than left blank. The API layer maps it to HTTP
    422 Unprocessable Content.
    """

    def __init__(self, token: str) -> None:
        super().__init__(
            f"Unknown lease template placeholder: {{{{ {token} }}}}.",
            code="leases.unknown_placeholder",
        )
        self.token = token


class MissingPlaceholderValueError(BKPropertyError):
    """Raised when a known placeholder has no value to fill at render time (Issue #37).

    Rendering fails loudly rather than emitting a blank where a real value belongs (e.g. a lease
    whose unit has no property address). Carries the placeholder so the API layer can build a
    clear message and map it to HTTP 422 Unprocessable Content.
    """

    def __init__(self, placeholder: str) -> None:
        super().__init__(
            f"No value to fill lease template placeholder {{{{ {placeholder} }}}}.",
            code="leases.missing_placeholder_value",
        )
        self.placeholder = placeholder


class PaymentNotFoundError(BKPropertyError):
    """Raised when a payment cannot be found on a lease by id (Issue #42).

    A payment that never existed, belongs to another lease, or is otherwise unreachable is
    indistinguishable from a missing one, so the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, payment_id: str) -> None:
        super().__init__(
            f"No such payment: {payment_id}.",
            code="payments.not_found",
        )
        self.payment_id = payment_id


class PaymentReversalError(BKPropertyError):
    """Raised when a payment cannot be reversed (Issue #42).

    A payment can be reversed at most once, and a reversing entry is never itself reversed:
    reversing a payment that is already a reversal, or one that has already been reversed, is
    rejected with the ledger left untouched. Carries the payment id and the reason so the API
    layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, payment_id: str, reason: str) -> None:
        super().__init__(
            f"Payment {payment_id} cannot be reversed: {reason}.",
            code="payments.not_reversible",
        )
        self.payment_id = payment_id
        self.reason = reason


class InvalidPaymentError(BKPropertyError):
    """Raised when a payment to record is not a positive amount (Issue #42).

    A recorded payment is money actually received, so its amount must be strictly positive — a
    zero or negative "payment" is meaningless (a correction is a *reversal*, not a negative
    recording). The API layer maps this to HTTP 422 Unprocessable Content.
    """

    def __init__(self, amount_minor: int) -> None:
        super().__init__(
            f"A recorded payment must be a positive amount; got {amount_minor}.",
            code="payments.invalid_amount",
        )
        self.amount_minor = amount_minor


class LateFeeRuleInvalidError(BKPropertyError):
    """Raised when a late-fee rule's configuration is inconsistent (Issue #44).

    A ``flat`` rule needs a flat amount and no percentage; a ``percentage`` rule needs a rate (and
    may carry a cap) and no flat amount; a ``property`` rule names its property and a ``global``
    rule names none; grace and amounts are non-negative. A rule that breaks one of these is
    rejected before it is stored so the sweep never reads an ambiguous rule. The API layer maps
    this to HTTP 422 Unprocessable Content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid late-fee rule: {reason}.",
            code="payments.late_fee_rule_invalid",
        )
        self.reason = reason


class LateFeeNotFoundError(BKPropertyError):
    """Raised when a late fee cannot be found on a lease by its charge id (Issue #44).

    A charge that is not a raised late fee on the lease — never existed, belongs to another lease,
    or is an ordinary charge with no late-fee application — is indistinguishable from a missing
    one, so the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, fee_charge_id: str) -> None:
        super().__init__(
            f"No such late fee: {fee_charge_id}.",
            code="payments.late_fee_not_found",
        )
        self.fee_charge_id = fee_charge_id


class LateFeeWaiverError(BKPropertyError):
    """Raised when a late fee cannot be waived (Issue #44).

    A late fee is waived at most once: waiving one that has already been waived is rejected with
    the ledger left untouched. Carries the fee's charge id and the reason so the API layer can
    build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, fee_charge_id: str, reason: str) -> None:
        super().__init__(
            f"Late fee {fee_charge_id} cannot be waived: {reason}.",
            code="payments.late_fee_not_waivable",
        )
        self.fee_charge_id = fee_charge_id
        self.reason = reason


class InvalidStatementPeriodError(BKPropertyError):
    """Raised when an owner-statement period is not a valid ``YYYY-MM`` calendar month (Issue #45).

    A statement is scoped to a calendar month so that periods tile the timeline without gaps or
    overlaps and each closing balance ties to the next opening one. A period that is malformed or
    names an impossible month is rejected before any figures are computed; the API layer maps this
    to HTTP 422 Unprocessable Content.
    """

    def __init__(self, period: str) -> None:
        super().__init__(
            f"Invalid statement period {period!r}; expected a calendar month as YYYY-MM.",
            code="payments.invalid_statement_period",
        )
        self.period = period


class MaintenanceRequestNotFoundError(BKPropertyError):
    """Raised when a maintenance request cannot be found by id (Issue #47).

    A missing (or soft-deleted) request is indistinguishable from one that never existed, so
    the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, request_id: str) -> None:
        super().__init__(
            f"No such maintenance request: {request_id}.",
            code="maintenance.request_not_found",
        )
        self.request_id = request_id


class UnitNotOccupiedByTenantError(BKPropertyError):
    """Raised when a tenant submits a request for a unit they do not currently occupy (Issue #47).

    A tenant may only raise a maintenance request against a unit bound to them by an *active*
    tenancy. Any other unit — one they never occupied, or a past tenancy that has ended — is
    rejected. The API layer maps this to HTTP 403 Forbidden and reveals nothing further, so it
    is indistinguishable from the unit not existing.
    """

    def __init__(self, tenant_id: str, unit_id: str) -> None:
        super().__init__(
            f"Tenant {tenant_id} does not currently occupy unit {unit_id}.",
            code="maintenance.unit_not_occupied",
        )
        self.tenant_id = tenant_id
        self.unit_id = unit_id


class MaintenanceStatusTransitionError(BKPropertyError):
    """Raised when a requested maintenance-request status change is not legal (Issue #47).

    The request lifecycle (``open`` -> ``triaged`` -> ``in_progress`` -> ``resolved`` ->
    ``closed``, with ``rejected`` as an early terminal state) moves only along defined edges;
    ``closed`` and ``rejected`` are terminal. Carries the ``current`` and ``target`` statuses so
    the API layer can build a clear message and map it to HTTP 409 Conflict, leaving the record
    untouched.
    """

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Illegal maintenance request status transition: {current} -> {target}.",
            code="maintenance.illegal_status_transition",
        )
        self.current = current
        self.target = target


class WorkOrderNotFoundError(BKPropertyError):
    """Raised when a work order cannot be found by id (Issue #48).

    A missing (or soft-deleted) work order is indistinguishable from one that never existed, so
    the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, work_order_id: str) -> None:
        super().__init__(
            f"No such work order: {work_order_id}.",
            code="maintenance.work_order_not_found",
        )
        self.work_order_id = work_order_id


class WorkOrderStatusTransitionError(BKPropertyError):
    """Raised when a requested work-order status change is not legal (Issue #48).

    A work order flows ``draft`` -> ``assigned`` -> ``in_progress`` -> ``completed`` and may be
    cancelled from any non-completed state; ``completed`` and ``cancelled`` are terminal. Carries
    the ``current`` and ``target`` statuses so the API layer can build a clear message and map it
    to HTTP 409 Conflict, leaving the record untouched.
    """

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Illegal work order status transition: {current} -> {target}.",
            code="maintenance.work_order_illegal_status_transition",
        )
        self.current = current
        self.target = target


class WorkOrderCompletionError(BKPropertyError):
    """Raised when a work order cannot be completed because required closing data is missing (Issue #48).

    Completing a job requires both a final cost and at least one completion note — a job is not
    "done" until what it actually cost and what was done are recorded. Carries the reason so the
    API layer can build a clear message and map it to HTTP 422 Unprocessable Content, leaving the
    record untouched.
    """

    def __init__(self, work_order_id: str, reason: str) -> None:
        super().__init__(
            f"Work order {work_order_id} cannot be completed: {reason}.",
            code="maintenance.work_order_incomplete_closure",
        )
        self.work_order_id = work_order_id
        self.reason = reason


class WorkOrderApprovalRequiredError(BKPropertyError):
    """Raised when a work order cannot be assigned because its estimate needs approval (Issue #50).

    An estimated cost above the approval threshold in force for the job's property holds the order
    in ``draft`` pending an owner decision — it may not move to ``assigned`` until it is approved.
    The order is left in ``draft`` with its approval status set to ``pending``. Carries the work
    order id, the estimate and the threshold (both in minor units) so the API layer can build a
    clear message and map it to HTTP 409 Conflict.
    """

    def __init__(
        self, work_order_id: str, estimate_minor: int, threshold_minor: int
    ) -> None:
        super().__init__(
            f"Work order {work_order_id} cannot be assigned: the estimate {estimate_minor} "
            f"(minor units) exceeds the approval threshold {threshold_minor} and requires approval.",
            code="maintenance.work_order_approval_required",
        )
        self.work_order_id = work_order_id
        self.estimate_minor = estimate_minor
        self.threshold_minor = threshold_minor


class WorkOrderReapprovalRequiredError(BKPropertyError):
    """Raised when a final cost overruns the approved estimate beyond the variance (Issue #50).

    A job whose estimate was approved may complete at up to the approved estimate plus the allowed
    variance; a final cost above that ceiling must be re-approved before the job can be completed,
    so an owner is never billed materially more than they agreed to without a fresh say. Carries the
    work order id, the final cost, the approved estimate and the allowed ceiling (all in minor
    units) so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(
        self,
        work_order_id: str,
        final_cost_minor: int,
        approved_estimate_minor: int,
        allowed_ceiling_minor: int,
    ) -> None:
        super().__init__(
            f"Work order {work_order_id} cannot be completed: the final cost {final_cost_minor} "
            f"(minor units) exceeds the approved estimate {approved_estimate_minor} beyond the "
            f"allowed variance (ceiling {allowed_ceiling_minor}); re-approval is required.",
            code="maintenance.work_order_reapproval_required",
        )
        self.work_order_id = work_order_id
        self.final_cost_minor = final_cost_minor
        self.approved_estimate_minor = approved_estimate_minor
        self.allowed_ceiling_minor = allowed_ceiling_minor


class WorkOrderApprovalError(BKPropertyError):
    """Raised when a work order cannot receive an approve / reject decision (Issue #50).

    An approval decision is only meaningful up to completion: a ``completed`` or ``cancelled`` job
    is terminal and its cost is settled, so approving or rejecting it is rejected with the record
    left untouched. Carries the work order id and the reason so the API layer can build a clear
    message and map it to HTTP 409 Conflict.
    """

    def __init__(self, work_order_id: str, reason: str) -> None:
        super().__init__(
            f"Work order {work_order_id} cannot be decided: {reason}.",
            code="maintenance.work_order_not_decidable",
        )
        self.work_order_id = work_order_id
        self.reason = reason


class VendorNotFoundError(BKPropertyError):
    """Raised when a vendor cannot be found by id (Issue #49).

    A missing (or soft-deleted) vendor is indistinguishable from one that never existed, so the
    API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, vendor_id: str) -> None:
        super().__init__(
            f"No such vendor: {vendor_id}.",
            code="vendors.not_found",
        )
        self.vendor_id = vendor_id


class DuplicateVendorError(BKPropertyError):
    """Raised when a vendor with the same email already exists (Issue #49).

    A live vendor per email keeps the directory to one row per firm; hitting the partial unique
    index (or the pre-check) surfaces here so the API layer returns HTTP 409 Conflict instead of a
    raw integrity error.
    """

    def __init__(self, email: str) -> None:
        super().__init__(
            f"A vendor already exists with email {email}.",
            code="vendors.duplicate_email",
        )
        self.email = email


class VendorInactiveError(BKPropertyError):
    """Raised when an inactive vendor is assigned to a new work order (Issue #49).

    A deactivated vendor is retired from the directory: they keep every historical job but may not
    take a *new* assignment. Assigning one is rejected with the work order left untouched. Carries
    the vendor id so the API layer can build a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, vendor_id: str) -> None:
        super().__init__(
            f"Vendor {vendor_id} is inactive and cannot be assigned new work.",
            code="vendors.inactive",
        )
        self.vendor_id = vendor_id


class VendorUserLinkConflictError(BKPropertyError):
    """Raised when linking a ``User`` to a vendor conflicts with an existing link (Issue #49).

    The link is one-to-one and idempotent: re-linking the *same* user is a no-op, but linking a
    user already bound to another vendor — or a vendor already bound to a different user — is a
    conflict the API layer maps to HTTP 409 Conflict.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="vendors.user_link_conflict")


class MessageAnchorNotFoundError(BKPropertyError):
    """Raised when a messaging thread is opened against a record that does not exist (Issue #68).

    A thread is anchored to a domain record (a unit, lease, work order or application); if no such
    record exists there is nothing to anchor to. Carries the anchor kind and id so the API layer
    can build a clear message and map it to HTTP 404 Not Found.
    """

    def __init__(self, anchor_type: str, anchor_id: str) -> None:
        super().__init__(
            f"No {anchor_type} record {anchor_id} to anchor a thread to.",
            code="messaging.anchor_not_found",
        )
        self.anchor_type = anchor_type
        self.anchor_id = anchor_id


class NotAThreadParticipantError(BKPropertyError):
    """Raised when a user who is not a participant acts on a thread (Issue #68).

    Participants are derived from a thread's anchor (the tenant, the owner, the assigned vendor,
    the applicant); anyone else may neither read nor post. The API layer maps this to HTTP 404 Not
    Found so a non-participant cannot even tell the thread exists.
    """

    def __init__(self, thread_id: str) -> None:
        super().__init__(
            f"Caller is not a participant of thread {thread_id}.",
            code="messaging.not_a_participant",
        )
        self.thread_id = thread_id


class MessageDraftNotFoundError(BKPropertyError):
    """Raised when a message draft cannot be found for the acting author (Issue #112).

    Drafts are private to their author, so a draft owned by someone else is indistinguishable from
    one that never existed: both surface here and the API layer maps this to HTTP 404 Not Found,
    revealing nothing about another user's drafts.
    """

    def __init__(self, draft_id: str) -> None:
        super().__init__(
            f"No message draft {draft_id} for this author.",
            code="messaging.draft_not_found",
        )
        self.draft_id = draft_id


class AlertDraftNotFoundError(BKPropertyError):
    """Raised when an alert draft cannot be found for the acting author (Issue #132 follow-up).

    Mirrors :class:`MessageDraftNotFoundError`: a draft owned by someone else is indistinguishable
    from one that never existed, both surfacing here and mapped to HTTP 404 Not Found.
    """

    def __init__(self, draft_id: str) -> None:
        super().__init__(
            f"No alert draft {draft_id} for this author.",
            code="alerts.draft_not_found",
        )
        self.draft_id = draft_id


class AudienceNotAuthorisedError(BKPropertyError):
    """Raised when a sender targets an announcement audience outside their authority (Issue #112).

    An audience resolves from RBAC + ownership: an admin alone may broadcast globally, an owner
    only to their own tenants or a property they own, a manager only within their managed scope.
    Targeting anything else — a property that is not theirs, a global broadcast without admin —
    raises this, which the API layer maps to HTTP 403 Forbidden. Carries the attempted audience
    kind (and reference, when any) so the message is specific without leaking who *is* in scope.
    """

    def __init__(self, audience_type: str, audience_ref: str | None = None) -> None:
        target = f" {audience_ref}" if audience_ref else ""
        super().__init__(
            f"Not authorised to send to the {audience_type}{target} audience.",
            code="messaging.audience_not_authorised",
        )
        self.audience_type = audience_type
        self.audience_ref = audience_ref


class EmptyAudienceError(BKPropertyError):
    """Raised when a resolved announcement audience contains no reachable recipient (Issue #112).

    The sender is authorised, but the audience resolves to an empty set (e.g. an owner with no
    account-linked tenants yet). There is no one to broadcast to, so rather than create an empty
    thread the API layer maps this to HTTP 409 Conflict with a clear message.
    """

    def __init__(self, audience_type: str) -> None:
        super().__init__(
            f"The {audience_type} audience resolves to no reachable recipients.",
            code="messaging.audience_empty",
        )
        self.audience_type = audience_type


class PeerNotReachableError(BKPropertyError):
    """Raised when a tenant tries to message someone they do not share a property with (Issue #126).

    Peer-to-peer messaging is authorised by a server-checked **co-occupancy** relationship: the
    sender and recipient must each hold an active tenancy in the same property. A recipient who is
    not a reachable neighbour — including one who does not exist or has no linked account — raises
    this, which the API layer maps to HTTP 403 Forbidden with a message that reveals nothing about
    whether the target exists or where they live (the negative case must never leak the directory).
    """

    def __init__(self) -> None:
        super().__init__(
            "You can only message a tenant of a property you share.",
            code="messaging.peer_not_reachable",
        )


class InAppNotificationNotFoundError(BKPropertyError):
    """Raised when an in-app notification cannot be found for the acting user (Issue #113).

    Notifications are private to the user they belong to, so one owned by someone else is
    indistinguishable from one that never existed: both surface here and the API layer maps this to
    HTTP 404 Not Found, revealing nothing about another user's feed.
    """

    def __init__(self, notification_id: str) -> None:
        super().__init__(
            f"No in-app notification {notification_id} for this user.",
            code="notifications.in_app_not_found",
        )
        self.notification_id = notification_id


class InspectionNotFoundError(BKPropertyError):
    """Raised when an inspection cannot be found by id (Issue #69).

    A missing (or soft-deleted) inspection is indistinguishable from one that never existed, so the
    API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, inspection_id: str) -> None:
        super().__init__(
            f"No such inspection: {inspection_id}.",
            code="inspections.not_found",
        )
        self.inspection_id = inspection_id


class InspectionStatusTransitionError(BKPropertyError):
    """Raised when a requested inspection status change is not legal (Issue #69).

    An inspection flows ``scheduled`` -> ``in_progress`` -> ``completed`` (and may complete straight
    from ``scheduled``); ``completed`` is terminal. Carries the ``current`` and ``target`` statuses
    so the API layer can build a clear message and map it to HTTP 409 Conflict, leaving the record
    untouched.
    """

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Illegal inspection status transition: {current} -> {target}.",
            code="inspections.illegal_status_transition",
        )
        self.current = current
        self.target = target


class InspectionCompletedError(BKPropertyError):
    """Raised when a completed inspection is edited (Issue #69).

    A completed inspection is an immutable, dated record: adding or changing items, photos or
    deductions, or re-completing it, is rejected — a correction is a *follow-up* inspection, not an
    edit. Carries the inspection id so the API layer can build a clear message and map it to HTTP
    409 Conflict.
    """

    def __init__(self, inspection_id: str) -> None:
        super().__init__(
            f"Inspection {inspection_id} is completed and immutable; create a follow-up "
            "inspection to record a correction.",
            code="inspections.completed_immutable",
        )
        self.inspection_id = inspection_id


class InspectionComparisonError(BKPropertyError):
    """Raised when a move-out cannot be compared against a baseline (Issue #69).

    A comparison needs a move-out inspection and a move-in baseline (either linked on
    ``compared_to_id`` or supplied). A comparison requested for a non-move-out inspection, or with no
    resolvable baseline, is rejected. Carries a reason so the API layer can build a clear message and
    map it to HTTP 409 Conflict.
    """

    def __init__(self, inspection_id: str, reason: str) -> None:
        super().__init__(
            f"Inspection {inspection_id} cannot be compared: {reason}.",
            code="inspections.comparison_error",
        )
        self.inspection_id = inspection_id
        self.reason = reason


class InspectionDeductionNotFoundError(BKPropertyError):
    """Raised when a proposed deduction cannot be found on an inspection by id (Issue #69).

    A deduction that never existed, belongs to another inspection, or is soft-deleted is
    indistinguishable from a missing one, so the API layer maps this to HTTP 404 Not Found.
    """

    def __init__(self, deduction_id: str) -> None:
        super().__init__(
            f"No such inspection deduction: {deduction_id}.",
            code="inspections.deduction_not_found",
        )
        self.deduction_id = deduction_id


class InspectionDeductionDecidedError(BKPropertyError):
    """Raised when an already-decided deduction is approved or rejected again (Issue #69).

    A proposed deduction is approved or rejected exactly once — approval posts the charge, rejection
    closes it out — so deciding one that is already ``approved`` or ``rejected`` is rejected with the
    ledger left untouched. Carries the deduction id and its current status so the API layer can build
    a clear message and map it to HTTP 409 Conflict.
    """

    def __init__(self, deduction_id: str, current_status: str) -> None:
        super().__init__(
            f"Inspection deduction {deduction_id} is already {current_status} and cannot be "
            "decided again.",
            code="inspections.deduction_already_decided",
        )
        self.deduction_id = deduction_id
        self.current_status = current_status


class DeductionLeaseNotFoundError(BKPropertyError):
    """Raised when an approved deduction has no lease to post its charge to (Issue #69).

    A deduction posts a ``maintenance`` charge to the tenant's *active* lease on the inspected unit;
    if no such lease can be resolved there is nowhere to post it, so the approval is rejected with
    nothing written. Carries the inspection id so the API layer can build a clear message and map it
    to HTTP 409 Conflict.
    """

    def __init__(self, inspection_id: str) -> None:
        super().__init__(
            f"No active lease found for inspection {inspection_id}'s unit to post the "
            "deduction charge to.",
            code="inspections.deduction_no_lease",
        )
        self.inspection_id = inspection_id


class DocumentInfectedError(BKPropertyError):
    """Raised when a document upload fails the virus scan on ingest (Issue #70).

    Scanning runs on the raw bytes before the document row is committed, so an infected upload is
    refused at the door rather than stored and later served. Carries the scanner's verdict so the
    API layer can build a clear message and map it to HTTP 422 Unprocessable Content, with nothing
    written to storage or the database.
    """

    def __init__(self, *, filename: str, scan_status: str) -> None:
        super().__init__(
            f"Document {filename!r} failed the virus scan (status: {scan_status}) and was rejected.",
            code="documents.infected",
        )
        self.filename = filename
        self.scan_status = scan_status


class DocumentChecksumMismatchError(BKPropertyError):
    """Raised when a stored document's bytes no longer match its recorded checksum (Issue #70).

    Every document records the SHA-256 of the bytes written at ingest; the download path re-hashes
    the bytes it reads and compares. A mismatch means the object was corrupted or tampered with out
    of band, so the download **fails loudly** rather than serving bad bytes — the API layer maps it
    to HTTP 409 Conflict. Carries the document id and both digests for the audit trail.
    """

    def __init__(self, document_id: str, *, expected: str, actual: str) -> None:
        super().__init__(
            f"Document {document_id} failed its integrity check: stored bytes do not match the "
            "recorded checksum.",
            code="documents.checksum_mismatch",
        )
        self.document_id = document_id
        self.expected = expected
        self.actual = actual


class EsignEnvelopeNotFoundError(BKPropertyError):
    """Raised when a signature envelope cannot be found by id (Issue #71).

    Carries the envelope id so the API layer can build a clear message and map it to HTTP 404.
    """

    def __init__(self, envelope_id: str) -> None:
        super().__init__(
            f"E-signature envelope {envelope_id} was not found.",
            code="esign.envelope_not_found",
        )
        self.envelope_id = envelope_id


class EsignEnvelopeStateError(BKPropertyError):
    """Raised when an action is attempted on an envelope in the wrong state (Issue #71).

    Sending an already-sent envelope, or voiding one that has already reached a terminal outcome
    (signed / declined / expired), is rejected with the record left untouched. Carries the current
    status and the attempted action so the API layer can build a clear message and map it to HTTP
    409 Conflict.
    """

    def __init__(self, envelope_id: str, current_status: str, action: str) -> None:
        super().__init__(
            f"E-signature envelope {envelope_id} cannot {action} from status {current_status}.",
            code="esign.illegal_state",
        )
        self.envelope_id = envelope_id
        self.current_status = current_status
        self.action = action


class LeaseSignatureRequiredError(BKPropertyError):
    """Raised when a lease is activated before it has been signed, where the property requires it.

    Where a property has ``esign_required`` set, a lease may only be activated once it carries a
    completed signature — electronic (a ``signed`` envelope) or, when the provider is unavailable,
    a recorded paper signature (Issue #71). Carries the lease id so the API layer can build a clear
    message and map it to HTTP 409 Conflict, leaving the lease a draft.
    """

    def __init__(self, lease_id: str) -> None:
        super().__init__(
            f"Lease {lease_id} requires a completed signature before it can be activated.",
            code="leases.signature_required",
        )
        self.lease_id = lease_id


class StripeNotConfiguredError(BKPropertyError):
    """Raised when a Stripe operation is attempted while the gateway is disabled (Issue #76).

    Creating a PaymentIntent or handling a webhook needs ``STRIPE_ENABLED`` set with keys present;
    when the gateway is off there is nothing to talk to. The API layer maps this to HTTP 503
    Service Unavailable — the feature is simply not turned on for this deployment (manual capture
    stays available as the fallback).
    """

    def __init__(self) -> None:
        super().__init__(
            "The Stripe payment gateway is not enabled for this deployment.",
            code="payments.stripe_disabled",
        )


class StripeSignatureError(BKPropertyError):
    """Raised when a Stripe webhook fails signature verification (Issue #76).

    An unsigned webhook, one signed with the wrong secret, or a replayed-and-tampered payload never
    reaches the ledger: the signature is checked before the event is parsed. The API layer maps this
    to HTTP 400 Bad Request so a forged call is rejected without side effects.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Stripe webhook signature verification failed: {reason}.",
            code="payments.stripe_bad_signature",
        )
        self.reason = reason


class StripePaymentError(BKPropertyError):
    """Raised when a Stripe API call to create or read a payment fails (Issue #76).

    Wraps the underlying Stripe error with a stable code and a message that never contains a key,
    so a card-network or API failure surfaces cleanly. The API layer maps this to HTTP 502 Bad
    Gateway — the upstream processor, not this service, is at fault.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Stripe payment operation failed: {reason}.",
            code="payments.stripe_error",
        )
        self.reason = reason


class PaystackNotConfiguredError(BKPropertyError):
    """Raised when a Paystack operation is attempted while the gateway is disabled (Issue #79).

    Initializing a transaction or handling a webhook needs ``PAYSTACK_ENABLED`` set with the secret
    key present; when the gateway is off there is nothing to talk to. The API layer maps this to HTTP
    503 Service Unavailable — the feature is simply not turned on for this deployment (manual capture
    stays available as the fallback).
    """

    def __init__(self) -> None:
        super().__init__(
            "The Paystack payment gateway is not enabled for this deployment.",
            code="payments.paystack_disabled",
        )


class PaystackSignatureError(BKPropertyError):
    """Raised when a Paystack webhook fails signature verification (Issue #79).

    An unsigned webhook, one signed with the wrong key, or a tampered payload never reaches the
    ledger: the ``x-paystack-signature`` HMAC-SHA512 over the raw body is checked before the event is
    parsed. The API layer maps this to HTTP 400 Bad Request so a forged call is rejected without side
    effects.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Paystack webhook signature verification failed: {reason}.",
            code="payments.paystack_bad_signature",
        )
        self.reason = reason


class PaystackPaymentError(BKPropertyError):
    """Raised when a Paystack API call to initialize or verify a transaction fails (Issue #79).

    Wraps the underlying HTTP/API error with a stable code and a message that never contains a key,
    so a card-network or API failure surfaces cleanly. The API layer maps this to HTTP 502 Bad
    Gateway — the upstream processor, not this service, is at fault.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Paystack payment operation failed: {reason}.",
            code="payments.paystack_error",
        )
        self.reason = reason
