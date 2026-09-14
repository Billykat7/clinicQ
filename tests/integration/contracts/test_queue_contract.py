"""The queue contract documents every route and every error the queue engine can refuse with (Issue 47).

``test_openapi_contracts.py`` (Issue 30's harness) already checks ``contracts/queue.yaml`` against the
application in both directions: every served route documented, every documented path served, every
status FastAPI knows about present. What FastAPI does not know about is a refusal a service raises,
and the queue engine's refusals are its most important promises to its consumers: "a done ticket
cannot change any more", "nobody is waiting", "the patient is already in that queue". So this file
adds the half the generic harness cannot see, read from the **shipped code** rather than a list typed
here:

* every error ``code`` the queue module can raise (read from its source: the ``*_CODE`` constants,
  the ``code="…"`` literals, and every value of the enums behind ``code=f"…"``) is named in the
  contract;
* every queue operation that changes state documents ``409``; every operation documents ``401``,
  ``403`` and ``404``;
* every documented ``409`` says which codes it can carry.

A code added to the engine without a line in the contract fails here, naming it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from src.commons.enums import JoinRefusal

_ROOT = Path(__file__).resolve().parents[3]
_QUEUE_MODULE = _ROOT / "src" / "modules" / "queue"
_CONTRACT = _ROOT / "contracts" / "queue.yaml"
_METHODS = ("get", "post", "put", "patch", "delete")

#: ``code=f"<prefix>.{reason}"`` sites: the prefix, and every reason the code can carry, read from the
#: source that raises them (their call sites) so a new reason is covered without editing this list.
_REASON_FAMILIES: dict[str, set[str]] = {
    "queue.join.": {refusal.value for refusal in JoinRefusal},
}


def _module_sources() -> dict[str, ast.Module]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(_QUEUE_MODULE.glob("*.py"))
    }


def _reasons_raised(tree: ast.Module, error_class: str) -> set[str]:
    """The first string argument of every ``raise <error_class>("reason", …)`` or ``_RefuseError(…)``."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == error_class
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def queue_error_codes() -> set[str]:
    """Every error code the queue module can put on the wire, read from its source."""
    codes: set[str] = set()
    families = dict(_REASON_FAMILIES)
    for tree in _module_sources().values():
        for node in ast.walk(tree):
            # ILLEGAL_TRANSITION_CODE: Final = "ticket.transition.illegal"
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.endswith("_CODE")
                and isinstance(node.value, ast.Constant)
            ):
                codes.add(str(node.value.value))
            # code="ticket.cancel.after_call"
            if isinstance(node, ast.keyword) and node.arg == "code":
                if (
                    isinstance(node.value, ast.Constant)
                    and str(node.value.value) != "http.not_found"
                ):
                    codes.add(str(node.value.value))
                if isinstance(node.value, ast.JoinedStr):
                    prefix = "".join(
                        str(part.value)
                        for part in node.value.values
                        if isinstance(part, ast.Constant)
                    )
                    if prefix == "ticket.transfer.":
                        families.setdefault(prefix, set()).update(
                            _reasons_raised(tree, "_RefuseError")
                        )
                    elif prefix == "ticket.priority.":
                        families.setdefault(prefix, set()).update(
                            _reasons_raised(tree, "PriorityRefusedError")
                        )
                    else:
                        families.setdefault(prefix, set())
    for prefix, reasons in families.items():
        assert reasons, (
            f"no reasons found for {prefix}…; the guard cannot see this family"
        )
        codes.update(f"{prefix}{reason}" for reason in reasons)
    return codes


def _contract() -> dict[str, Any]:
    return yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, operations in _contract()["paths"].items()
        for method, operation in operations.items()
        if method in _METHODS
    ]


def test_every_error_code_the_queue_engine_raises_is_in_the_contract() -> None:
    """A refusal a consumer can receive and cannot look up is a contract with a hole in it."""
    text = _CONTRACT.read_text(encoding="utf-8")
    codes = queue_error_codes()
    missing = sorted(code for code in codes if code not in text)
    assert len(codes) >= 15, sorted(
        codes
    )  # the reading found the engine's codes, not nothing
    assert missing == [], f"queue.yaml does not name these error codes: {missing}"


def test_every_queue_operation_documents_its_refusals() -> None:
    """401, 403 and 404 on every operation; 409 on every one that changes state."""
    findings = []
    for method, path, operation in _operations():
        responses = set(operation.get("responses", {}))
        if path == "/patients/me/tickets" and method == "get":
            required = {"401", "403"}  # a patient's own list: nothing to be not found
        else:
            required = {"401", "403", "404"}
        if method != "get":
            required.add("409")
        if missing := required - responses:
            findings.append(f"{method.upper()} {path}: {sorted(missing)}")
    assert findings == []


def test_every_documented_409_names_the_codes_it_can_carry() -> None:
    """A 409 that says only "conflict" leaves a channel adapter guessing what to tell the patient."""
    document = _contract()
    code_pattern = re.compile(r"(queue\.join|ticket\.[a-z_]+)\.[a-z_]+")

    def resolved(response: dict[str, Any]) -> dict[str, Any]:
        ref = response.get("$ref", "")
        return (
            document["components"]["responses"][ref.rsplit("/", 1)[1]]
            if ref
            else response
        )

    missing = [
        f"{method.upper()} {path}"
        for method, path, operation in _operations()
        if "409" in operation.get("responses", {})
        and not code_pattern.search(
            yaml.safe_dump(resolved(operation["responses"]["409"]))
        )
    ]
    assert missing == []


def test_the_code_reader_fails_on_a_code_the_contract_does_not_name() -> None:
    """A guard that cannot fail passes forever: a new code in the engine is found missing."""
    codes = queue_error_codes() | {"ticket.transition.brand_new"}
    text = _CONTRACT.read_text(encoding="utf-8")
    assert [code for code in codes if code not in text] == [
        "ticket.transition.brand_new"
    ]
