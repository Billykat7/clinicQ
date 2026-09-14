"""The hand-written API contracts, checked against the application in both directions (Issue 30).

A contract nobody checks is a document that was true once. This suite is the check, and it is
written to be **reused**: the discovery (Issue 38), queue (47), notifications (71), channels (79)
and reporting (94) contracts each add one :class:`Contract` entry below and inherit every test in
this file. Nothing else about them changes.

Three things are asserted, and the third is the one people forget:

1. **Every route the application serves is documented.** Adding a route without writing it down
   fails the suite and names the method and path.
2. **Every documented path has a handler.** A contract that describes a route nobody wrote is worse
   than no contract, because a consumer builds against it.
3. **Every example validates against the schema it is an example of.** An example is the part a
   consumer copies, so an example the API would refuse is a trap. Checked with ``jsonschema``
   against the contract's *own* schemas, resolving its own ``$ref``s.

The drift check compares **method + path + documented status codes**, not response bodies: the
contract is a promise about the shape of the surface, and the module tests are what prove the
behaviour behind it. A status the application can return and the contract does not mention is a
finding; the reverse is not, because a contract may legitimately document a refusal the current
handler happens not to be able to produce yet (an example is a route whose 409 arrives with a later
issue). That asymmetry is deliberate and named here so nobody "fixes" it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from src.main import create_app

_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = _ROOT / "contracts"

#: The HTTP methods a contract describes. ``head`` and ``options`` are framework plumbing.
METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True, slots=True)
class Contract:
    """One hand-written contract, and which part of the application it speaks for.

    ``excluded`` names the routes that live under ``prefix`` but belong to **another** module's
    contract. Each entry carries its reason, so the list is a set of decisions a reviewer can read
    rather than a way of making the drift test quiet.
    """

    name: str
    #: The file, under ``contracts/``.
    filename: str
    #: Every application route starting with this is in scope.
    prefix: str
    #: ``{path: reason}`` for routes under the prefix this contract does not own.
    excluded: dict[str, str] = field(default_factory=dict)
    #: A regular expression a route must also match, for a contract that owns a **resource** found
    #: under several prefixes rather than one prefix. The queue contract owns every ``/tickets``
    #: route, whether it hangs off ``/sites``, ``/clinics`` or ``/patients`` (Issue 40). A contract
    #: without one never claims a route that a patterned contract owns, so each route has exactly one
    #: contract and nobody has to keep an exclusion list in step with another file.
    pattern: str = ""

    def owns(self, path: str) -> bool:
        """Whether this contract is the one responsible for documenting ``path``."""
        if not path.startswith(self.prefix) or path in self.excluded:
            return False
        if self.pattern:
            return re.search(self.pattern, path) is not None
        return not any(
            other.pattern and re.search(other.pattern, path) for other in CONTRACTS
        )

    @property
    def path(self) -> Path:
        """Where the file is."""
        return CONTRACTS_DIR / self.filename

    def document(self) -> dict[str, Any]:
        """The parsed contract."""
        return yaml.safe_load(self.path.read_text(encoding="utf-8"))


#: Every contract this project ships. **Add one line per milestone's contract**; the tests below
#: are parameterised over this list and need no other change.
CONTRACTS: tuple[Contract, ...] = (
    Contract(
        name="sites",
        filename="sites.yaml",
        prefix="/api/v1/sites",
        excluded={
            "/api/v1/sites/{site_id}/audit/events": (
                "a clinic's own audit trail: it sits under this prefix but belongs to the audit "
                "module (Issue 20), and will be documented by that module's contract"
            ),
        },
    ),
    Contract(name="discovery", filename="discovery.yaml", prefix="/api/v1/clinics"),
    # Every ticket route, wherever it hangs (Issue 40 starts it; Issue 47 completes it).
    Contract(
        name="queue", filename="queue.yaml", prefix="/api/v1", pattern=r"/tickets(/|$)"
    ),
)

_IDS = [contract.name for contract in CONTRACTS]


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    """The application's own OpenAPI document, built once for the module."""
    with TestClient(create_app()) as client:
        return client.get("/openapi.json").json()  # type: ignore[no-any-return]


def _server_prefix(document: dict[str, Any]) -> str:
    """The path prefix the contract's ``servers`` entry adds to each of its paths."""
    servers = document.get("servers") or [{"url": ""}]
    return str(servers[0].get("url", "")).rstrip("/")


def _contract_operations(document: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
    """``{(METHOD, full path): {documented status codes}}`` for one contract."""
    prefix = _server_prefix(document)
    out: dict[tuple[str, str], set[str]] = {}
    for path, operations in document.get("paths", {}).items():
        for method, operation in operations.items():
            if method not in METHODS:
                continue
            out[(method.upper(), f"{prefix}{path}")] = set(
                operation.get("responses", {})
            )
    return out


def _application_operations(
    document: dict[str, Any], contract: Contract
) -> dict[tuple[str, str], set[str]]:
    """``{(METHOD, path): {status codes}}`` for the routes ``contract`` is responsible for."""
    out: dict[tuple[str, str], set[str]] = {}
    for path, operations in document.get("paths", {}).items():
        if not contract.owns(path):
            continue
        for method, operation in operations.items():
            if method not in METHODS:
                continue
            out[(method.upper(), path)] = set(operation.get("responses", {}))
    return out


def _undocumented_statuses(served: set[str], documented: set[str]) -> set[str]:
    """The **concrete** statuses in ``served`` that ``documented`` does not account for.

    Wildcards (``4XX``, ``5XX``, ``default``) are skipped, and it is worth being exact about why
    rather than leaving it to look like a loophole. They are what this application's global error
    handlers register on *every* operation; they are not a per-route promise, and requiring the
    literal ``"5XX"`` in each operation would make the contract say less, not more.

    The consequence is that this particular check is narrower than it looks: FastAPI's document
    knows the success code and the ``422`` it generates from a request body, and it does **not**
    know about the ``403``/``404``/``409`` a handler raises. So this catches a success code moving
    (a ``201`` quietly becoming a ``200``) and a body's validation appearing or going away — and
    the **error** half of the contract is proved by ``tests/integration/sites/test_sites_module.py``,
    which drives every documented refusal over real HTTP. Both are needed; neither is sufficient.
    """
    concrete = {status for status in served if not status.upper().endswith("XX")}
    concrete.discard("default")
    return concrete - documented


def _readable(operations: set[tuple[str, str]]) -> str:
    """Findings as lines somebody can act on."""
    return "\n".join(f"  {method} {path}" for method, path in sorted(operations))


# --------------------------------------------------------------------------------------
# The drift check, both directions
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_every_route_the_application_serves_is_documented(
    contract: Contract, openapi: dict[str, Any]
) -> None:
    """Adding an undocumented route fails here, and the failure names it."""
    served = set(_application_operations(openapi, contract))
    documented = set(_contract_operations(contract.document()))
    undocumented = served - documented
    assert not undocumented, (
        f"{contract.filename} does not document these routes, which the application serves:\n"
        + _readable(undocumented)
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_every_documented_path_has_a_handler(
    contract: Contract, openapi: dict[str, Any]
) -> None:
    """The other direction: a contract describing a route nobody wrote is worse than none."""
    served = set(_application_operations(openapi, contract))
    documented = set(_contract_operations(contract.document()))
    phantom = documented - served
    assert not phantom, (
        f"{contract.filename} documents these routes, which the application does not serve:\n"
        + _readable(phantom)
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_every_status_the_application_can_return_is_documented(
    contract: Contract, openapi: dict[str, Any]
) -> None:
    """Error responses, not only the happy path.

    One direction only: a status the application returns and the contract omits is a finding; a
    status the contract documents and the handler cannot currently produce is not (see the module
    docstring for why).
    """
    served = _application_operations(openapi, contract)
    documented = _contract_operations(contract.document())
    findings = []
    for (method, path), statuses in sorted(served.items()):
        missing = _undocumented_statuses(
            statuses, documented.get((method, path), set())
        )
        if missing:
            findings.append(f"  {method} {path}: {sorted(missing)}")
    assert not findings, (
        f"{contract.filename} omits these status codes the application can return:\n"
        + "\n".join(findings)
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_the_exclusions_are_real_routes_with_reasons(
    contract: Contract, openapi: dict[str, Any]
) -> None:
    """An exclusion list nobody can review is a way of making this suite quiet.

    Every entry has to name a route the application actually serves (so a stale one is a failure
    rather than dead weight) and carry a reason.
    """
    for path, reason in contract.excluded.items():
        assert path in openapi["paths"], f"{path} is excluded but is not a route"
        assert reason.strip(), f"{path} is excluded with no reason"


# --------------------------------------------------------------------------------------
# The examples
# --------------------------------------------------------------------------------------


def _validator(
    document: dict[str, Any], schema: dict[str, Any]
) -> Draft202012Validator:
    """A validator for one schema, able to resolve the contract's own ``$ref``s.

    OpenAPI 3.1's schemas *are* JSON Schema 2020-12, and a ``$ref`` in one of them points at
    ``#/components/schemas/...`` — a pointer **relative to the document**. A validator built on a
    bare sub-schema has that sub-schema as its resource root, so the pointer resolves to nowhere.
    Carrying ``components`` alongside the schema makes the bundle the root, which is what the
    pointer was written against. Sibling keywords beside ``$ref`` are legal in 2020-12, so this
    works for a schema that is itself only a reference.
    """
    return Draft202012Validator(
        {**schema, "components": document.get("components", {})}
    )


def _examples(document: dict[str, Any]) -> list[tuple[str, Any, dict[str, Any]]]:
    """Every ``(where, example, schema)`` triple in the contract.

    Walks the document rather than listing places examples may appear, so an example added to a new
    kind of node is covered without anyone remembering this function. An ``example`` with no schema
    beside it is skipped: there is nothing to validate it against.
    """
    found: list[tuple[str, Any, dict[str, Any]]] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            if "example" in node and "schema" in node:
                found.append((where, node["example"], node["schema"]))
            elif "example" in node and {"type", "properties", "$ref", "enum"} & set(
                node
            ):
                found.append(
                    (
                        where,
                        node["example"],
                        {k: v for k, v in node.items() if k != "example"},
                    )
                )
            for key, value in node.items():
                walk(value, f"{where}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    walk(document, "")
    return found


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_every_example_validates_against_its_own_schema(contract: Contract) -> None:
    """An example is the part a consumer copies, so one the API would refuse is a trap."""
    document = contract.document()
    failures: list[str] = []
    for where, example, schema in _examples(document):
        for error in _validator(document, schema).iter_errors(example):
            failures.append(f"  {where}: {error.message}")
    assert not failures, (
        f"{contract.filename} has examples its own schemas refuse:\n"
        + "\n".join(failures)
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_the_contract_has_examples_at_all(contract: Contract) -> None:
    """A guard that cannot fail passes forever, and so does one with nothing to check.

    The frontend and channel teams build from the examples, so the contract carries a meaningful
    number of them and this test fails if somebody strips them out.
    """
    assert len(_examples(contract.document())) >= 15


@pytest.mark.parametrize("contract", CONTRACTS, ids=_IDS)
def test_every_reference_in_the_contract_resolves(contract: Contract) -> None:
    """A ``$ref`` to a component that does not exist is a document nobody can generate from."""
    document = contract.document()
    components = document.get("components", {})
    missing: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/"):
                section, name = ref.removeprefix("#/components/").split("/", 1)
                if name not in components.get(section, {}):
                    missing.append(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    assert not missing, (
        f"{contract.filename} has unresolvable references: {sorted(set(missing))}"
    )


# --------------------------------------------------------------------------------------
# The harness can fail
# --------------------------------------------------------------------------------------

_A_CONTRACT_MISSING_A_ROUTE = {
    "servers": [{"url": "/api/v1"}],
    "paths": {"/things": {"get": {"responses": {"200": {"description": "ok"}}}}},
}
_AN_APPLICATION_WITH_TWO = {
    "paths": {
        "/api/v1/things": {"get": {"responses": {"200": {}}}},
        "/api/v1/things/{id}": {"get": {"responses": {"200": {}, "404": {}}}},
    }
}


def test_the_drift_check_finds_an_undocumented_route() -> None:
    """The criterion, demonstrated: the check fails and names the route."""
    contract = Contract(name="fixture", filename="none.yaml", prefix="/api/v1/things")
    served = set(_application_operations(_AN_APPLICATION_WITH_TWO, contract))
    documented = set(_contract_operations(_A_CONTRACT_MISSING_A_ROUTE))
    assert served - documented == {("GET", "/api/v1/things/{id}")}


def test_the_drift_check_finds_a_documented_route_with_no_handler() -> None:
    """And the other direction."""
    contract = Contract(name="fixture", filename="none.yaml", prefix="/api/v1/things")
    served = set(_application_operations({"paths": {}}, contract))
    documented = set(_contract_operations(_A_CONTRACT_MISSING_A_ROUTE))
    assert documented - served == {("GET", "/api/v1/things")}


def test_a_ticket_route_belongs_to_the_queue_contract_wherever_it_hangs() -> None:
    """A patterned contract owns its resource across prefixes, and the prefix contract cedes it.

    ``/sites/{site_id}/tickets`` sits under the sites prefix but is the queue contract's, so neither
    contract can quietly skip it and neither can claim it twice.
    """
    by_name = {contract.name: contract for contract in CONTRACTS}
    for path in (
        "/api/v1/sites/{site_id}/tickets",
        "/api/v1/sites/{site_id}/queues/{queue_id}/tickets",
        "/api/v1/clinics/{site_id}/queues/{queue_id}/tickets",
        "/api/v1/patients/me/tickets",
    ):
        owners = [contract.name for contract in CONTRACTS if contract.owns(path)]
        assert owners == ["queue"], (path, owners)
    assert by_name["sites"].owns("/api/v1/sites/{site_id}/queues")
    assert not by_name["queue"].owns("/api/v1/sites/{site_id}/queues")


def test_the_example_check_fails_on_an_example_its_schema_refuses() -> None:
    """A fixture that is exactly the mistake this test exists to catch."""
    broken = {
        "components": {
            "schemas": {
                "Thing": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                    "example": {"name": 42},
                }
            }
        }
    }
    failures = [
        error.message
        for _where, example, schema in _examples(broken)
        for error in _validator(broken, schema).iter_errors(example)
    ]
    assert failures and "42" in failures[0]


def test_a_discovery_route_added_without_documenting_it_is_caught() -> None:
    """Issue 38's criterion on the real application, not a fixture: add a route, and it is named.

    A route added to the running app under ``/api/v1/clinics`` (a bulk export of the directory is
    exactly the kind of thing that would appear quietly) is undocumented in ``discovery.yaml``, and
    the same comparison the drift test runs names it.
    """
    app = create_app()
    app.add_api_route("/api/v1/clinics/export", lambda: {"items": []}, methods=["GET"])
    (discovery,) = [c for c in CONTRACTS if c.name == "discovery"]
    served = set(_application_operations(app.openapi(), discovery))
    documented = set(_contract_operations(discovery.document()))
    assert served - documented == {("GET", "/api/v1/clinics/export")}
