"""Non-negotiable 5, enforced: enums on the wire, Johannesburg in the business layer (Issue 4).

``docs/guideline.md`` promises that breaking this rule fails the build rather than relying on a
reviewer noticing. This file is that promise. It parses every Python file the product ships
(``src/``) and the scripts that write product data (``scripts/db/``), and fails, naming the file
and line, on any of three things:

1. **A naive datetime.** ``datetime.now()``, ``datetime.today()``, ``date.today()``,
   ``datetime.utcnow()`` and friends take the server's clock zone, which is UTC in the container
   and SAST on a laptop, so the same code puts a ticket on a different service day depending on
   where it runs. Use :func:`src.commons.time.now_sast` / :func:`~src.commons.time.business_date`.
2. **"Now" in UTC outside a module that a standard obliges to use it.** UTC is allowed where a
   standard requires it (JWT ``exp``/``iat``, an X.509 ``notAfter``), and only in the modules listed
   in :data:`UTC_REQUIRED_BY_A_STANDARD`, each with the standard named. An entry that no longer
   uses UTC fails too, so the list cannot rot into a blanket exemption.
3. **A magic status string.** A string literal compared with, assigned to, or passed as a
   status-like value (``status``, ``*_status``, ``role``, ``source``, ``sector``,
   ``display_mode``, ``channel``, ``state``), or compared with anything when it spells a ClinicQ
   wire value (``"no_show"``, ``"walk_in"``, ``"number_only"``…). Use the enum member.

``ruff``'s ``DTZ`` rules catch most of rule 1 at lint time as well; this guard is the part that
also covers rules 2 and 3, reads the whole tree in one place, and proves it can fail: the second
half of the file feeds each rule the violations it exists to catch, and the near-misses it must let
through. A guard that cannot fail passes forever.

It reads source, not behaviour, and asserts nothing about rendered HTML
(``docs/IDE/RULES/testing-strategy.mdc``). Migrations (``alembic/``) are deliberately out of scope:
a migration freezes the literal values of its day, which is correct even after an enum changes.
"""

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from src.commons.enums import (
    DisplayMode,
    SiteSector,
    TicketSource,
    TicketStatus,
    UserRole,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: What the guard reads: everything the product ships, and the scripts that write product data.
SCANNED_ROOTS = ("src", "scripts/db")

#: Modules that may take "now" in UTC, and the standard that obliges each to. Adding an entry is a
#: team decision (``docs/guideline.md``), made in review where this diff is visible.
UTC_REQUIRED_BY_A_STANDARD: dict[str, str] = {
    "src/core/security.py": (
        "JWT iat/exp/nbf are NumericDate values: seconds since the Unix epoch, in UTC "
        "(RFC 7519 section 2)."
    ),
    "src/core/refresh_token_policy.py": (
        "A refresh token's expiry is checked against the JWT exp it is issued with (RFC 7519)."
    ),
    "src/api/v1/routes/auth.py": (
        "Session and refresh-token timestamps are compared with JWT exp/iat (RFC 7519) and read "
        "back as UTC from SQLite. Issue 15 rebuilds the session model and revisits last_login."
    ),
    "src/core/verification.py": (
        "Compares with last_verification_email_sent_at, which auth.py and rbac_admin.py write as "
        "UTC and SQLite reads back naive."
    ),
    "src/api/v1/routes/rbac_admin.py": (
        "Writes last_verification_email_sent_at for the admin resend, which verification.py "
        "reads as UTC (above). Every other timestamp in the module is SAST."
    ),
    "src/modules/documents/download_links.py": (
        "A signed download link is a JWT: exp/iat are UTC NumericDates (RFC 7519 section 2)."
    ),
    "src/core/health.py": (
        "An X.509 certificate's notAfter is UTC (RFC 5280 section 4.1.2.5)."
    ),
}

#: Names whose value is a status-like wire value. ``mode`` alone is left out on purpose: it is
#: also ``open(path, mode="rb")``; the one ClinicQ mode is ``display_mode``.
_WIRE_NAME = re.compile(
    r"^(?:\w+_)?(?:status|state|role|source|sector|channel)$|^display_mode$"
)

#: The ClinicQ enums whose values must never appear as bare literals in a comparison.
_CLINICQ_WIRE_ENUMS: tuple[type[StrEnum], ...] = (
    SiteSector,
    TicketStatus,
    TicketSource,
    DisplayMode,
)

#: Every ClinicQ wire value, plus the ClinicQ roles (the kernel's ``user``/``admin`` are words too
#: common to police by value; the name rule still catches ``role == "admin"``).
_WIRE_VALUES: frozenset[str] = frozenset(
    member.value for enum in _CLINICQ_WIRE_ENUMS for member in enum
) | frozenset(
    {
        UserRole.PATIENT,
        UserRole.RECEPTIONIST,
        UserRole.NURSE_DOCTOR,
        UserRole.CLINIC_MANAGER,
        UserRole.PLATFORM_ADMIN,
    }
)

#: Names of zone arguments that mean UTC.
_UTC_NAMES = frozenset({"UTC"})


@dataclass(frozen=True)
class Violation:
    """One rule broken at one place in the tree."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        """``path:line: message``, the format an editor can jump to."""
        return f"{self.path}:{self.line}: {self.message}"


# ── reading the tree ─────────────────────────────────────────────────────────────────────


def _python_files() -> Iterator[Path]:
    """Every Python file under the scanned roots, in a stable order."""
    for root in SCANNED_ROOTS:
        yield from sorted((REPO_ROOT / root).rglob("*.py"))


def _relative(path: Path) -> str:
    """The repo-relative POSIX path used in messages and allowlist keys."""
    return path.relative_to(REPO_ROOT).as_posix()


def _scan(finder: Callable[[ast.Module, str], list[Violation]]) -> list[Violation]:
    """Run one rule over every scanned file."""
    violations: list[Violation] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(finder(tree, _relative(path)))
    return violations


# ── rules 1 and 2: datetimes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _DatetimeNames:
    """The local names a module binds to the ``datetime`` module and its classes."""

    classes: dict[str, str]
    modules: frozenset[str]
    utc: frozenset[str]
    timezone: frozenset[str]
    zoneinfo: frozenset[str]


def _datetime_names(tree: ast.Module) -> _DatetimeNames:
    """Resolve imports, so ``from datetime import datetime as dt`` is still ``datetime``."""
    classes: dict[str, str] = {}
    modules: set[str] = set()
    utc: set[str] = set(_UTC_NAMES)
    timezone: set[str] = set()
    zoneinfo: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in {"datetime", "date"}:
                    classes[local] = alias.name
                elif alias.name == "UTC":
                    utc.add(local)
                elif alias.name == "timezone":
                    timezone.add(local)
        elif isinstance(node, ast.ImportFrom) and node.module == "zoneinfo":
            zoneinfo.update(
                a.asname or a.name for a in node.names if a.name == "ZoneInfo"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                a.asname or a.name for a in node.names if a.name == "datetime"
            )
    return _DatetimeNames(
        classes,
        frozenset(modules),
        frozenset(utc),
        frozenset(timezone),
        frozenset(zoneinfo),
    )


def _class_of(node: ast.expr, names: _DatetimeNames) -> str | None:
    """Return ``"datetime"``/``"date"`` when ``node`` names one of those classes, else None."""
    if isinstance(node, ast.Name):
        return names.classes.get(node.id)
    if (
        isinstance(node, ast.Attribute)
        and node.attr in {"datetime", "date"}
        and isinstance(node.value, ast.Name)
        and node.value.id in names.modules
    ):
        return node.attr
    return None


def _zone_argument(call: ast.Call, position: int) -> ast.expr | None:
    """The ``tz``/``tzinfo`` argument of a call, by keyword or position, if one was passed."""
    for keyword in call.keywords:
        if keyword.arg in {"tz", "tzinfo"}:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _is_none(node: ast.expr | None) -> bool:
    """True when no zone was passed, or the zone passed is the literal ``None``."""
    return node is None or (isinstance(node, ast.Constant) and node.value is None)


def _is_utc(node: ast.expr | None, names: _DatetimeNames) -> bool:
    """True when a zone argument means UTC: ``UTC``, ``timezone.utc`` or ``ZoneInfo("UTC")``."""
    match node:
        case ast.Name(id=name):
            return name in names.utc
        case ast.Attribute(value=ast.Name(id=owner), attr="utc"):
            return owner in names.timezone
        case ast.Attribute(value=ast.Attribute(attr="timezone"), attr="utc"):
            return True
        case ast.Attribute(value=ast.Name(id=owner), attr="UTC"):
            return owner in names.modules
        case ast.Call(func=ast.Name(id=func), args=[ast.Constant(value=str(zone))]):
            return func in names.zoneinfo and zone in {"UTC", "Etc/UTC"}
        case _:
            return False


#: Methods that return a naive value however they are called: the fix is a different method.
_ALWAYS_NAIVE = {
    ("datetime", "utcnow"): "datetime.utcnow() is naive; use now_sast()",
    ("datetime", "utcfromtimestamp"): "datetime.utcfromtimestamp() is naive",
    ("datetime", "today"): "datetime.today() is naive; use now_sast()",
    ("date", "today"): "date.today() reads the server's zone; use business_date()",
    ("date", "fromtimestamp"): "date.fromtimestamp() reads the server's zone",
}

#: ``(class, method) -> position of the zone argument``: naive only when that argument is missing.
_NAIVE_WITHOUT_ZONE = {
    ("datetime", "now"): 0,
    ("datetime", "fromtimestamp"): 1,
    ("datetime", "combine"): 2,
}


def find_naive_datetimes(tree: ast.Module, path: str) -> list[Violation]:
    """Rule 1: every call in ``tree`` that produces a naive datetime."""
    names = _datetime_names(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # The constructor: datetime(2026, 9, 11, 8, 0) with no tzinfo (the 8th positional).
        if _class_of(func, names) == "datetime":
            if _is_none(_zone_argument(node, 7)):
                found.append(
                    Violation(path, node.lineno, "naive datetime(...): pass tzinfo=")
                )
            continue
        if not isinstance(func, ast.Attribute):
            continue
        cls = _class_of(func.value, names)
        if cls is None:
            continue
        key = (cls, func.attr)
        if key in _ALWAYS_NAIVE:
            found.append(Violation(path, node.lineno, _ALWAYS_NAIVE[key]))
        elif key in _NAIVE_WITHOUT_ZONE and _is_none(
            _zone_argument(node, _NAIVE_WITHOUT_ZONE[key])
        ):
            found.append(
                Violation(
                    path,
                    node.lineno,
                    f"naive {cls}.{func.attr}(): pass a zone, or use now_sast()",
                )
            )
        elif key == ("datetime", "strptime") and not _has_offset_directive(node):
            found.append(
                Violation(
                    path,
                    node.lineno,
                    "datetime.strptime() without %z is naive: attach the zone",
                )
            )
    return found


def _has_offset_directive(call: ast.Call) -> bool:
    """True when a ``strptime`` format is a literal containing ``%z`` (so the result is aware)."""
    if len(call.args) < 2:
        return False
    fmt = call.args[1]
    return (
        isinstance(fmt, ast.Constant)
        and isinstance(fmt.value, str)
        and "%z" in fmt.value
    )


def find_utc_now(tree: ast.Module, path: str) -> list[Violation]:
    """Rule 2 (the finding half): every "now" or timestamp conversion taken in UTC."""
    names = _datetime_names(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        key = (_class_of(node.func.value, names), node.func.attr)
        if key in _NAIVE_WITHOUT_ZONE and _is_utc(
            _zone_argument(node, _NAIVE_WITHOUT_ZONE[key]), names
        ):
            found.append(
                Violation(
                    path,
                    node.lineno,
                    f"datetime.{node.func.attr}() in UTC: use now_sast(), or list this "
                    "module in UTC_REQUIRED_BY_A_STANDARD with the standard that requires it",
                )
            )
    return found


# ── rule 3: magic status strings ─────────────────────────────────────────────────────────


def _literal_strings(node: ast.expr) -> list[str] | None:
    """The string literal(s) ``node`` is made of: a ``str`` constant, or a tuple/list/set of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if (
        isinstance(node, (ast.Tuple, ast.List, ast.Set))
        and node.elts
        and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        )
    ):
        return [e.value for e in node.elts if isinstance(e, ast.Constant)]
    return None


def _wire_name(node: ast.AST) -> str | None:
    """The status-like name ``node`` refers to (``ticket.status``, ``status``, ``row["role"]``)."""
    match node:
        case ast.Attribute(attr=name) | ast.Name(id=name):
            pass
        case ast.Subscript(slice=ast.Constant(value=str(name))):
            pass
        case _:
            return None
    return name if _WIRE_NAME.match(name) else None


def _magic(path: str, node: ast.AST, what: str, literal: str) -> Violation:
    """A magic-string violation, quoting the literal so the fix is obvious."""
    return Violation(
        path,
        getattr(node, "lineno", 0),
        f"magic string {literal!r} {what}: use the enum member",
    )


def find_magic_strings(tree: ast.Module, path: str) -> list[Violation]:
    """Rule 3: string literals standing in for a status, role, source, sector or display mode."""
    found: list[Violation] = []
    for node in ast.walk(tree):
        match node:
            case ast.Compare(left=left, comparators=comparators):
                operands = [left, *comparators]
                named = next((n for n in map(_wire_name, operands) if n), None)
                for operand in operands:
                    for literal in _literal_strings(operand) or []:
                        if named:
                            found.append(
                                _magic(path, node, f"compared with {named}", literal)
                            )
                        elif literal in _WIRE_VALUES:
                            found.append(_magic(path, node, "compared", literal))
            case ast.Assign(targets=targets, value=value):
                literals = _literal_strings(value) or []
                for target in targets:
                    if (named := _wire_name(target)) and literals:
                        found.append(
                            _magic(path, node, f"assigned to {named}", literals[0])
                        )
            case ast.AnnAssign(target=target, value=ast.expr() as value):
                if (named := _wire_name(target)) and (
                    literals := _literal_strings(value)
                ):
                    found.append(
                        _magic(path, node, f"assigned to {named}", literals[0])
                    )
            case ast.keyword(arg=str(arg), value=value):
                if _WIRE_NAME.match(arg) and (literals := _literal_strings(value)):
                    found.append(_magic(path, value, f"passed as {arg}=", literals[0]))
            case ast.Dict(keys=keys, values=values):
                for key, value in zip(keys, values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and _WIRE_NAME.match(key.value)
                        and (literals := _literal_strings(value))
                    ):
                        found.append(
                            _magic(
                                path, key, f"stored under {key.value!r}", literals[0]
                            )
                        )
            case ast.arguments():
                positional = [*node.posonlyargs, *node.args]
                # ``defaults`` align with the *last* positional parameters; ``kw_defaults`` holds
                # None for a keyword-only parameter without one.
                with_defaults = positional[len(positional) - len(node.defaults) :]
                pairs = [
                    *zip(with_defaults, node.defaults, strict=True),
                    *(
                        (a, d)
                        for a, d in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                        if d
                    ),
                ]
                for arg, default in pairs:
                    if _WIRE_NAME.match(arg.arg) and default is not None:
                        for literal in _literal_strings(default) or []:
                            found.append(
                                _magic(
                                    path, arg, f"as the default of {arg.arg}", literal
                                )
                            )
            case ast.Match(subject=subject, cases=cases) if _wire_name(subject):
                for case in cases:
                    for pattern in ast.walk(case.pattern):
                        if isinstance(pattern, ast.MatchValue) and (
                            literals := _literal_strings(pattern.value)
                        ):
                            found.append(
                                _magic(path, pattern, "matched in a case", literals[0])
                            )
    # The enum module is where the values are *defined*; nothing else is exempt.
    return [] if path == "src/commons/enums.py" else found


# ── the guard ────────────────────────────────────────────────────────────────────────────


def _report(violations: list[Violation]) -> str:
    """One violation per line, for a failure message a person can act on."""
    return "\n".join(str(v) for v in violations)


def test_no_naive_datetime_anywhere_in_the_tree() -> None:
    """Rule 1: a naive ``datetime.now()`` (or any of its cousins) fails the build."""
    violations = _scan(find_naive_datetimes)
    assert not violations, (
        "Naive datetimes (business time is Africa/Johannesburg: use src.commons.time):\n"
        + _report(violations)
    )


def test_utc_is_taken_only_where_a_standard_requires_it() -> None:
    """Rule 2: UTC "now" only in the modules listed with the standard that obliges them."""
    violations = [
        v for v in _scan(find_utc_now) if v.path not in UTC_REQUIRED_BY_A_STANDARD
    ]
    assert not violations, "UTC outside UTC_REQUIRED_BY_A_STANDARD:\n" + _report(
        violations
    )


def test_every_utc_exemption_is_still_needed() -> None:
    """An exempt module that stopped using UTC must leave the list, or it exempts the next edit."""
    using_utc = {v.path for v in _scan(find_utc_now)}
    stale = sorted(set(UTC_REQUIRED_BY_A_STANDARD) - using_utc)
    assert not stale, (
        f"No longer take UTC; remove from UTC_REQUIRED_BY_A_STANDARD: {stale}"
    )


def test_no_magic_status_string_anywhere_in_the_tree() -> None:
    """Rule 3: a literal status, role, source, sector or display mode fails the build."""
    violations = _scan(find_magic_strings)
    assert not violations, (
        "Magic strings (import the enum from src.commons.enums):\n"
        + _report(violations)
    )


# ── the guard can fail ───────────────────────────────────────────────────────────────────


def _check(
    finder: Callable[[ast.Module, str], list[Violation]], source: str
) -> list[str]:
    """Run one rule over a snippet and return its messages."""
    return [v.message for v in finder(ast.parse(source), "snippet.py")]


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\nstamp = datetime.now()",
        "from datetime import datetime\nstamp = datetime.now(tz=None)",
        "import datetime\nstamp = datetime.datetime.now()",
        "import datetime as dt\nstamp = dt.datetime.now()",
        "from datetime import datetime as DateTime\nstamp = DateTime.now()",
        "from datetime import datetime\nstamp = datetime.utcnow()",
        "from datetime import datetime\nstamp = datetime.today()",
        "from datetime import date\nday = date.today()",
        "from datetime import datetime\nstamp = datetime.fromtimestamp(0)",
        "from datetime import datetime\nstamp = datetime(2026, 9, 11, 8, 0)",
        "from datetime import datetime, time\nstamp = datetime.combine(day, time.min)",
        "from datetime import datetime\nstamp = datetime.strptime(raw, '%Y-%m-%d %H:%M')",
    ],
)
def test_the_datetime_rule_catches_every_naive_form(source: str) -> None:
    """Each naive form is reported, however ``datetime`` was imported."""
    assert _check(find_naive_datetimes, source), source


@pytest.mark.parametrize(
    "source",
    [
        "from src.commons.time import now_sast\nstamp = now_sast()",
        "from datetime import datetime\nfrom src.commons.time import APP_TIMEZONE\n"
        "stamp = datetime.now(APP_TIMEZONE)",
        "from datetime import datetime\nstamp = datetime.now(tz=APP_TIMEZONE)",
        "from datetime import datetime\nstamp = datetime.fromtimestamp(0, APP_TIMEZONE)",
        "from datetime import datetime\nstamp = datetime(2026, 9, 11, tzinfo=APP_TIMEZONE)",
        "from datetime import datetime\nstamp = datetime.strptime(raw, '%Y-%m-%dT%H:%M%z')",
        # Another object's ``now`` is not the datetime class's.
        "clock = FakeClock()\nstamp = clock.now()",
    ],
)
def test_the_datetime_rule_allows_aware_datetimes(source: str) -> None:
    """Aware construction is not reported."""
    assert _check(find_naive_datetimes, source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import UTC, datetime\nstamp = datetime.now(UTC)",
        "from datetime import datetime, timezone\nstamp = datetime.now(timezone.utc)",
        "import datetime\nstamp = datetime.datetime.now(datetime.timezone.utc)",
        "import datetime\nstamp = datetime.datetime.now(datetime.UTC)",
        "from datetime import datetime\nfrom zoneinfo import ZoneInfo\n"
        "stamp = datetime.now(ZoneInfo('UTC'))",
        "from datetime import UTC, datetime\nstamp = datetime.fromtimestamp(0, tz=UTC)",
    ],
)
def test_the_utc_rule_catches_utc_now(source: str) -> None:
    """Each spelling of "now in UTC" is found, so the allowlist is the only way past."""
    assert _check(find_utc_now, source), source


@pytest.mark.parametrize(
    "source",
    [
        # Comparisons, against a status-like name or a ClinicQ value, and membership tests.
        'if ticket.status == "waiting":\n    pass',
        'if "called" != ticket.status:\n    pass',
        'if ticket.status in ("done", "no_show"):\n    pass',
        'if row["status"] == "cancelled":\n    pass',
        'if user.role == "admin":\n    pass',
        'if site.display_mode == "full":\n    pass',
        'if value == "walk_in":\n    pass',
        'if value == "number_only":\n    pass',
        # Writes: attribute, local, annotated, subscript.
        'ticket.status = "called"',
        'status = "waiting"',
        'ticket_status: str = "in_progress"',
        'payload["source"] = "ussd"',
        # Calls, payloads, defaults and match statements.
        'Ticket(status="waiting", source="web")',
        'query.filter_by(sector="public")',
        'body = {"status": "done", "role": "receptionist"}',
        'def join(source: str = "walk_in") -> None:\n    pass',
        'def find(*, status: str = "waiting") -> None:\n    pass',
        'match ticket.status:\n    case "called":\n        pass',
    ],
)
def test_the_magic_string_rule_catches_each_shape(source: str) -> None:
    """Each way a status literal sneaks in is reported."""
    assert _check(find_magic_strings, source), source


@pytest.mark.parametrize(
    "source",
    [
        "if ticket.status == TicketStatus.WAITING:\n    pass",
        "if ticket.status in TICKET_TERMINAL_STATUSES:\n    pass",
        "ticket.status = TicketStatus.CALLED",
        "Ticket(status=TicketStatus.WAITING, source=TicketSource.WEB)",
        'body = {"status": HealthStatus.OK}',
        # Words that are not wire values, on names that are not status-like.
        'if name == "waiting room":\n    pass',
        'open(path, mode="rb")',
        'logger.info("status changed", extra={"path": "/health"})',
        "status_code = 404",
    ],
)
def test_the_magic_string_rule_allows_enums_and_ordinary_strings(source: str) -> None:
    """Enum members and unrelated strings are not reported."""
    assert _check(find_magic_strings, source) == []


def test_violations_name_the_file_and_line() -> None:
    """A failure points at the offending line, so it can be fixed without searching."""
    [violation] = find_magic_strings(
        ast.parse('\n\nticket.status = "called"'), "src/modules/queue/router.py"
    )
    assert str(violation).startswith(
        "src/modules/queue/router.py:3: magic string 'called'"
    )
