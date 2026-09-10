"""CI guard: no un-offloaded blocking I/O inside ``async def`` handlers (Issue #81, Phase 4).

The bug behind Issue #81 (and the earlier ``/admin/logs`` hang) is a *blocking* leaf call — a
synchronous SMTP send, an S3 round-trip, a payment-gateway HTTP call or a synchronous DB query —
executed **inside an ``async def``** and therefore on the event loop, freezing the whole worker for
its duration. The fix already applied off-loads that work with ``asyncio.to_thread`` (external I/O)
or ``await AsyncSession.execute`` (the migrated DB path). This test promotes that rule into an
automated guard so a regression fails the build instead of silently returning.

It is a static (AST) scan of ``src/`` — it never opens a socket — with two checks:

* **External-I/O offload (all async functions).** A curated set of known-blocking leaf helpers
  (SMTP sends, the S3 log readers, the Stripe/Paystack gateway calls) may only ever be *referenced*
  as an argument to ``asyncio.to_thread`` — the correct offload passes them as a bare name. So any
  **direct call** to one of them inside an ``async def`` is the anti-pattern, and is flagged. This
  check is green across the whole codebase today; it exists to keep it that way.

* **DB session non-blocking (migrated functions).** For each function already migrated to the async
  request-path session (Issue #81 Phase 2 onward), a synchronous-session DB method call
  (``db.execute`` / ``db.commit`` / …) that is not ``await``-ed is flagged. The migrated set is an
  explicit allowlist that grows as the module-by-module rollout (Phase 3) proceeds, so the DB guard
  ratchets forward without ever regressing an already-async path.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"


# Known-blocking leaf helpers that must be off-loaded with ``asyncio.to_thread`` and therefore must
# never be *called* directly inside an ``async def``. Matched on the callable's final name, so both
# ``send_activation_email(...)`` and ``stripe_gateway.create_payment_intent(...)`` are covered.
_BLOCKING_SINKS: frozenset[str] = frozenset(
    {
        # Transactional email — synchronous smtplib + STARTTLS under the hood.
        "send_activation_email",
        "send_otp_email",
        "send_password_reset_email",
        "send_email_change_verification_email",
        "send_acknowledgement",
        # Structured-log object store — synchronous boto3 S3 calls.
        "list_s3_application_logs",
        "get_s3_log_object_text",
        # Payment gateways — synchronous Stripe/Paystack HTTP calls.
        "create_payment_intent",
        "build_reconciliation_report",
        "initialize_transaction",
    }
)


# Synchronous-session DB methods that, inside a migrated async function, must be awaited.
_SYNC_DB_METHODS: frozenset[str] = frozenset(
    {
        "execute",
        "scalar",
        "scalars",
        "commit",
        "flush",
        "refresh",
        "get",
        "merge",
        "add",
        "delete",
    }
)


# Functions already migrated to the async request-path session (Issue #81). Their DB calls must be
# awaited. Grows module by module as the rollout proceeds; keyed by (src-relative path, function).
_MIGRATED_ASYNC_DB_FUNCTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("modules/properties/service.py", "search_public_listings_async"),
        ("modules/properties/router.py", "search_public_listings"),
    }
)


def _leaf_name(func: ast.expr) -> str | None:
    """Return the final identifier of a call target (``foo`` or ``mod.foo`` -> ``foo``)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _calls_in_own_scope(fn: ast.AsyncFunctionDef) -> list[ast.Call]:
    """Every ``Call`` lexically inside ``fn`` but not inside a nested (async) function it defines.

    Nested function bodies are separate scopes — a helper defined and returned, say — so they are
    excluded to keep the check about what *this* coroutine runs on the loop.
    """
    calls: list[ast.Call] = []

    def _visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue  # a different scope
            if isinstance(child, ast.Call):
                calls.append(child)
            _visit(child)

    _visit(fn)
    return calls


def _awaited_call_ids(fn: ast.AsyncFunctionDef) -> set[int]:
    """Ids of ``Call`` nodes that are the direct operand of an ``await`` within ``fn``."""
    awaited: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))
    return awaited


def _iter_async_functions(
    tree: ast.AST,
) -> list[ast.AsyncFunctionDef]:
    """All ``async def`` nodes anywhere in a parsed module."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]


def _scan_source_tree() -> tuple[list[str], list[str]]:
    """Scan ``src/`` and return ``(sink_violations, db_violations)`` as human-readable strings."""
    sink_violations: list[str] = []
    db_violations: list[str] = []

    for path in sorted(_SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(_SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in _iter_async_functions(tree):
            calls = _calls_in_own_scope(fn)

            # Check 1 — a blocking sink called directly (not offloaded via to_thread).
            for call in calls:
                if _leaf_name(call.func) in _BLOCKING_SINKS:
                    sink_violations.append(
                        f"{rel}:{call.lineno} async '{fn.name}' calls blocking "
                        f"'{_leaf_name(call.func)}' directly; offload it with asyncio.to_thread"
                    )

            # Check 2 — migrated functions must await their sync-session DB methods.
            if (rel, fn.name) in _MIGRATED_ASYNC_DB_FUNCTIONS:
                awaited = _awaited_call_ids(fn)
                for call in calls:
                    func = call.func
                    if (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "db"
                        and func.attr in _SYNC_DB_METHODS
                        and id(call) not in awaited
                    ):
                        db_violations.append(
                            f"{rel}:{call.lineno} migrated async '{fn.name}' calls "
                            f"'db.{func.attr}(...)' without await (blocks the loop)"
                        )

    return sink_violations, db_violations


def test_no_blocking_sink_called_directly_in_async_functions() -> None:
    """No known-blocking leaf (SMTP/S3/Stripe/Paystack) is called directly inside an ``async def``."""
    sink_violations, _ = _scan_source_tree()
    assert not sink_violations, "Blocking I/O on the event loop:\n" + "\n".join(
        sink_violations
    )


def test_migrated_async_db_functions_await_their_queries() -> None:
    """Every migrated async function awaits its DB calls (no sync session on the loop)."""
    _, db_violations = _scan_source_tree()
    assert not db_violations, (
        "Un-awaited DB I/O in a migrated async path:\n" + "\n".join(db_violations)
    )


def test_guard_detects_a_direct_blocking_call() -> None:
    """Meta-test: the sink checker flags a synthetic direct blocking call (guard is not a no-op)."""
    source = (
        "import asyncio\n"
        "async def handler():\n"
        "    send_activation_email(to_email='x')\n"  # the anti-pattern
    )
    tree = ast.parse(source)
    fn = _iter_async_functions(tree)[0]
    offending = [
        c for c in _calls_in_own_scope(fn) if _leaf_name(c.func) in _BLOCKING_SINKS
    ]
    assert offending, "checker failed to flag a direct blocking sink call"


def test_guard_accepts_an_offloaded_call() -> None:
    """Meta-test: passing the same sink to asyncio.to_thread is not flagged (correct offload)."""
    source = (
        "import asyncio\n"
        "async def handler():\n"
        "    await asyncio.to_thread(send_activation_email, to_email='x')\n"
    )
    tree = ast.parse(source)
    fn = _iter_async_functions(tree)[0]
    offending = [
        c for c in _calls_in_own_scope(fn) if _leaf_name(c.func) in _BLOCKING_SINKS
    ]
    assert not offending, "checker wrongly flagged an offloaded call"
