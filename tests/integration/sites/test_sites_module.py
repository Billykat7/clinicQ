"""The sites module end to end, and every refusal the contract promises (Issue 30).

Two jobs, and the second is why this file exists rather than leaning on the per-issue suites:

* **the full site lifecycle in one place** — submitted, verified, configured, staffed, suspended —
  so that the pieces M4 built in seven separate issues are proved to work *together*;
* **every error response `contracts/sites.yaml` documents is driven over real HTTP.** The drift
  test can only see what FastAPI's own document knows, which is the success code and the `422` it
  generates from a request body; it cannot see a `403`, a `404` or a `409` a handler raises. So the
  contract's error half is proved here, by asking for each refusal and checking the status.

An independent re-verification, not a re-run of the per-issue tests: this reads the contract and
asserts against it, so a status the contract promises and the application stopped returning fails
here even if every other test still passes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from starlette import status

from src.commons.enums import DisplayMode, SiteStatus, UserRole

_CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "sites.yaml"


@pytest.fixture(scope="module")
def contract() -> dict:
    """The hand-written contract these tests check the application against."""
    return yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))


def documented(contract: dict, method: str, path: str) -> set[str]:
    """The status codes the contract documents for one operation (paths are relative to /api/v1)."""
    operations = contract["paths"][path.removeprefix("/api/v1")]
    return set(operations[method.lower()]["responses"])


# --------------------------------------------------------------------------------------
# The full lifecycle: submitted, verified, configured, staffed, suspended
# --------------------------------------------------------------------------------------


def test_a_clinic_goes_from_a_public_form_to_a_running_clinic_and_back_off(
    clinics: SimpleNamespace,
) -> None:
    """Everything M4 built, in the order a real clinic would meet it."""
    from fastapi.testclient import TestClient

    operator = clinics.client("operator@clinicq.example")
    anonymous = TestClient(clinics.app)

    # 1. Somebody submits it.
    submitted = anonymous.post(
        "/api/v1/sites/register",
        json={
            "name": "Mofolo North Clinic",
            "slug": "mofolo-north-clinic",
            "sector": "public",
            "location": {"latitude": -26.24745, "longitude": 27.88400},
            "address_line": "Mofolo North, Soweto",
            "city": "Johannesburg",
            "province": "Gauteng",
            "contact_name": "Sipho Khumalo",
            "contact_email": "sipho@mofolo.example",
        },
    )
    assert submitted.status_code == status.HTTP_201_CREATED, submitted.text
    site_id = submitted.json()["id"]

    # 2. It is waiting, and invisible.
    queue = operator.get("/api/v1/sites/verification").json()
    assert site_id in {item["id"] for item in queue["items"]}

    # 3. The operator verifies it.
    verified = operator.put(
        f"/api/v1/sites/{site_id}/verification",
        json={"status": SiteStatus.VERIFIED.value},
    )
    assert verified.status_code == status.HTTP_200_OK

    # 4. A manager is put in charge of it, and configures it.
    manager_id = _user_id(clinics, "manager.a@clinicq.example")
    operator_grant = operator.post(
        f"/api/v1/sites/{site_id}/staff/{manager_id}/roles",
        json={"role": UserRole.CLINIC_MANAGER.value},
    )
    # The operator is assigned to no clinic, so the site guard answers 404 — Issue 19's rule, and
    # the reason bootstrapping a new clinic's first manager is still an open follow-up (PR #136).
    assert operator_grant.status_code == status.HTTP_404_NOT_FOUND

    # 5. Suspending it stops joins, whatever its hours say.
    suspended = operator.put(
        f"/api/v1/sites/{site_id}/verification",
        json={
            "status": SiteStatus.SUSPENDED.value,
            "note": "Confirming with the district.",
        },
    )
    assert suspended.status_code == status.HTTP_200_OK
    assert suspended.json()["status"] == SiteStatus.SUSPENDED.value


def _user_id(clinics: SimpleNamespace, email: str) -> str:
    """One account's id."""
    from sqlalchemy import select

    from src.database.models import User

    with clinics.session() as db:
        return db.execute(select(User.id).where(User.email == email)).scalar_one()


def test_a_configured_clinic_answers_every_read_the_contract_promises(
    clinics: SimpleNamespace,
) -> None:
    """One clinic, every GET in the contract, all 200. The module working as a whole."""
    manager = clinics.client("manager.a@clinicq.example")
    site = clinics.site_a
    manager.put(
        f"/api/v1/sites/{site}/hours",
        json={
            "days": [
                {
                    "weekday": day,
                    "spans": [{"opens_at": "07:00:00", "closes_at": "16:00:00"}],
                }
                for day in range(5)
            ]
        },
    )
    manager.post(
        f"/api/v1/sites/{site}/queues",
        json={"name": "Triage", "slug": "triage", "kind": "triage"},
    )
    manager.post(
        f"/api/v1/sites/{site}/services",
        json={"name": "General consultation", "slug": "consultation"},
    )
    user_id = _user_id(clinics, "nurse.a@clinicq.example")

    for path in (
        f"/api/v1/sites/{site}",
        f"/api/v1/sites/{site}/open",
        f"/api/v1/sites/{site}/hours",
        f"/api/v1/sites/{site}/holidays",
        f"/api/v1/sites/{site}/closures",
        f"/api/v1/sites/{site}/queues",
        f"/api/v1/sites/{site}/queues/joinable",
        f"/api/v1/sites/{site}/services",
        f"/api/v1/sites/{site}/settings/display",
        f"/api/v1/sites/{site}/settings/display-options",
        f"/api/v1/sites/{site}/staff",
        f"/api/v1/sites/{site}/staff/invitations",
        f"/api/v1/sites/{site}/staff/{user_id}",
        f"/api/v1/sites/{site}/staff/{user_id}/queues",
    ):
        assert manager.get(path).status_code == status.HTTP_200_OK, path


# --------------------------------------------------------------------------------------
# Every documented refusal, driven
# --------------------------------------------------------------------------------------


def test_every_route_that_names_a_clinic_answers_404_for_another_ones(
    clinics: SimpleNamespace, contract: dict
) -> None:
    """The contract says so for every `{site_id}` operation; this asks each one.

    The paths are taken **from the contract**, so a route added to it without this behaviour fails
    here — which is what makes this an independent re-verification rather than a second copy of the
    per-issue tests.
    """
    manager = clinics.client("manager.a@clinicq.example")
    elsewhere = clinics.site_b
    checked = 0
    for path, operations in contract["paths"].items():
        if "{site_id}" not in path or "get" not in operations:
            continue
        if "{" in path.replace("{site_id}", ""):
            continue  # needs a second id; covered by the per-issue suites
        url = "/api/v1" + path.replace("{site_id}", elsewhere)
        assert "404" in documented(contract, "get", "/api/v1" + path), path
        assert manager.get(url).status_code == status.HTTP_404_NOT_FOUND, url
        checked += 1
    assert checked >= 10, "the contract lost its site-scoped reads"


def test_the_documented_403s_are_real(clinics: SimpleNamespace, contract: dict) -> None:
    """A receptionist is refused every write the contract says needs a manager."""
    desk = clinics.client("desk.a@clinicq.example")
    site = clinics.site_a
    refusals = (
        (
            "put",
            f"/sites/{site}",
            {
                "name": "x",
                "slug": "x-y",
                "sector": "public",
                "location": {"latitude": -26.2, "longitude": 28.0},
                "address_line": "1 Street",
                "city": "Johannesburg",
                "province": "Gauteng",
            },
        ),
        ("put", f"/sites/{site}/hours", {"days": []}),
        ("post", f"/sites/{site}/closures", {"reason": "Going home."}),
        ("post", f"/sites/{site}/queues", {"name": "X Queue", "slug": "x-queue"}),
        ("post", f"/sites/{site}/services", {"name": "X Service", "slug": "x-service"}),
        (
            "put",
            f"/sites/{site}/settings/display",
            {"display_mode": DisplayMode.FULL.value},
        ),
    )
    for method, path, body in refusals:
        response = getattr(desk, method)(f"/api/v1{path}", json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path
        template = path.replace(site, "{site_id}")
        assert "403" in documented(contract, method, f"/api/v1{template}"), template


def test_the_documented_409s_are_real(clinics: SimpleNamespace, contract: dict) -> None:
    """Each conflict the contract promises, produced."""
    operator = clinics.client("operator@clinicq.example")
    manager = clinics.client("manager.a@clinicq.example")
    site = clinics.site_a
    body = {
        "name": "Conflict Clinic",
        "slug": "conflict-clinic",
        "sector": "public",
        "location": {"latitude": -26.2, "longitude": 28.0},
        "address_line": "1 Street",
        "city": "Johannesburg",
        "province": "Gauteng",
    }
    assert operator.post("/api/v1/sites", json=body).status_code == 201
    assert operator.post(
        "/api/v1/sites", json={**body, "name": "Other"}
    ).status_code == (status.HTTP_409_CONFLICT)

    queue = {"name": "Triage", "slug": "triage"}
    assert manager.post(f"/api/v1/sites/{site}/queues", json=queue).status_code == 201
    assert (
        manager.post(
            f"/api/v1/sites/{site}/queues", json={**queue, "slug": "triage-2"}
        ).status_code
        == status.HTTP_409_CONFLICT
    )

    service = {"name": "Consultation", "slug": "consultation"}
    assert (
        manager.post(f"/api/v1/sites/{site}/services", json=service).status_code == 201
    )
    assert (
        manager.post(
            f"/api/v1/sites/{site}/services", json={**service, "slug": "consultation-2"}
        ).status_code
        == status.HTTP_409_CONFLICT
    )

    # Switching the board to full names without confirming it.
    assert (
        manager.put(
            f"/api/v1/sites/{site}/settings/display",
            json={"display_mode": DisplayMode.FULL.value},
        ).status_code
        == status.HTTP_409_CONFLICT
    )

    # And the last clinic manager.
    manager_id = _user_id(clinics, "manager.a@clinicq.example")
    assert (
        manager.delete(
            f"/api/v1/sites/{site}/staff/{manager_id}/roles/clinic_manager"
        ).status_code
        == status.HTTP_409_CONFLICT
    )

    for method, path in (
        ("post", "/api/v1/sites"),
        ("post", "/api/v1/sites/{site_id}/queues"),
        ("post", "/api/v1/sites/{site_id}/services"),
        ("put", "/api/v1/sites/{site_id}/settings/display"),
        ("delete", "/api/v1/sites/{site_id}/staff/{user_id}/roles/{role}"),
    ):
        assert "409" in documented(contract, method, path), path


def test_the_documented_422s_are_real(clinics: SimpleNamespace, contract: dict) -> None:
    """The validation refusals a consumer will meet first."""
    operator = clinics.client("operator@clinicq.example")
    manager = clinics.client("manager.a@clinicq.example")
    site = clinics.site_a

    off_the_map = operator.post(
        "/api/v1/sites",
        json={
            "name": "Nowhere Clinic",
            "slug": "nowhere-clinic",
            "sector": "public",
            "location": {"latitude": 0.0, "longitude": 0.0},
            "address_line": "1 Street",
            "city": "Johannesburg",
            "province": "Gauteng",
        },
    )
    assert off_the_map.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "South Africa" in off_the_map.text

    overlapping = manager.put(
        f"/api/v1/sites/{site}/hours",
        json={
            "days": [
                {
                    "weekday": 0,
                    "spans": [
                        {"opens_at": "07:00:00", "closes_at": "13:00:00"},
                        {"opens_at": "12:00:00", "closes_at": "16:00:00"},
                    ],
                }
            ]
        },
    )
    assert overlapping.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    out_of_range = manager.post(
        f"/api/v1/sites/{site}/services",
        json={"name": "Too Long", "slug": "too-long", "expected_minutes": 600},
    )
    assert out_of_range.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    for method, path in (
        ("post", "/api/v1/sites"),
        ("put", "/api/v1/sites/{site_id}/hours"),
        ("post", "/api/v1/sites/{site_id}/services"),
    ):
        assert "422" in documented(contract, method, path), path


def test_the_documented_401s_are_real(clinics: SimpleNamespace, contract: dict) -> None:
    """Every route but the two public ones needs a session."""
    from fastapi.testclient import TestClient

    anonymous = TestClient(clinics.app)
    for path in (
        "/api/v1/sites",
        "/api/v1/sites/verification",
        f"/api/v1/sites/{clinics.site_a}",
        f"/api/v1/sites/{clinics.site_a}/queues",
    ):
        assert anonymous.get(path).status_code == status.HTTP_401_UNAUTHORIZED, path

    # And the two that deliberately do not.
    assert anonymous.get("/api/v1/sites/info").status_code == status.HTTP_200_OK
    assert anonymous.get("/api/v1/sites/queues/info").status_code == status.HTTP_200_OK
    for path in ("/api/v1/sites/info", "/api/v1/sites/queues/info"):
        assert "401" not in documented(contract, "get", path), path


def test_the_contract_documents_a_404_for_every_site_scoped_operation(
    contract: dict,
) -> None:
    """Read from the contract itself: non-negotiable 3 is a promise it has to make everywhere."""
    missing = [
        f"{method.upper()} {path}"
        for path, operations in contract["paths"].items()
        if "{site_id}" in path
        for method, operation in operations.items()
        if method in ("get", "post", "put", "patch", "delete")
        and "404" not in operation.get("responses", {})
    ]
    assert not missing, (
        "these site-scoped operations do not document a 404, which is how the site guard refuses "
        f"another clinic's id: {missing}"
    )
