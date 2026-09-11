"""``seed_dev_data`` refuses production and never duplicates (Issue 8).

Both proofs run the script the way a developer does, as ``python -m scripts.db.seed_dev_data`` in a
subprocess, configured only by environment variables (which beat the local ``.env``).

* **Refusal** needs no database: the guard runs before a connection is opened. The targets are
  hosts that do not resolve, so a script that tried to connect would fail with a traceback and a
  different exit code, not refuse with 2.
* **Idempotence** runs twice against a migrated throwaway PostgreSQL database (``postgres``).
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from scripts.db.seed_dev_data import DEMO_STAFF

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_seed(**env: str) -> subprocess.CompletedProcess[str]:
    """Run the seed script as a developer would, with ``env`` on top of a clean environment.

    From an empty working directory, so the developer's ``.env`` (read relative to the working
    directory) cannot add settings the test did not choose: Issue 12's boot guard would otherwise
    refuse a local ``DEBUG=true`` before the seed's own refusal is reached.
    """
    base = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO_ROOT),
        "ENVIRONMENT": "development",
        "AWS_S3_LOGGING_ENABLED": "false",
    }
    with tempfile.TemporaryDirectory() as no_env_file_here:
        return subprocess.run(
            [sys.executable, "-m", "scripts.db.seed_dev_data"],
            cwd=no_env_file_here,
            env=base | env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        (
            {
                "DATABASE_URL": "postgresql://clinicq:secret@db.prod.clinicq.invalid:5432/clinicq"
            },
            "not a local development database",
        ),
        (
            {"DATABASE_URL": "postgresql://clinicq:secret@10.20.30.40:5432/clinicq"},
            "not a local development database",
        ),
        (
            {
                "DATABASE_URL": "postgresql://btk_user:change-me@localhost:5432/clinicq_prod"
            },
            "looks like production",
        ),
        (
            {
                "DATABASE_URL": "postgresql://btk_user:change-me@localhost:5432/btk",
                "ENVIRONMENT": "production",
                "JWT_SECRET": "a-production-secret-that-is-long-enough-to-pass",
            },
            "seeded in development only",
        ),
    ],
    ids=[
        "production-host",
        "remote-ip",
        "prod-database-name",
        "production-environment",
    ],
)
def test_the_seed_refuses_anything_but_a_local_development_database(
    env: dict[str, str], reason: str
) -> None:
    """Exit 2, the reason on stderr, nothing on stdout: it never got as far as seeding."""
    result = _run_seed(**env)

    assert result.returncode == 2, result.stderr
    assert reason in result.stderr
    assert "nothing was touched" in result.stderr
    assert "Seeding" not in result.stdout


@pytest.mark.postgres
def test_a_second_run_creates_no_duplicates(migrated_database: URL) -> None:
    """Run twice: four accounts the first time, the same four untouched the second."""
    url = migrated_database.render_as_string(hide_password=False)

    started = time.perf_counter()
    first = _run_seed(DATABASE_URL=url)
    elapsed = time.perf_counter() - started
    second = _run_seed(DATABASE_URL=url)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Staff accounts: 4 created, 0 updated, 0 unchanged" in first.stdout
    assert "Staff accounts: 0 created, 0 updated, 4 unchanged" in second.stdout
    assert elapsed < 30

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            users = conn.execute(
                text(
                    "SELECT count(*) FROM clinicq.\"user\" WHERE email LIKE '%@clinicq.example'"
                )
            ).scalar_one()
            assignments = conn.execute(
                text(
                    'SELECT count(*) FROM clinicq.user_roles r JOIN clinicq."user" u '
                    "ON u.id = r.user_id WHERE u.email LIKE '%@clinicq.example'"
                )
            ).scalar_one()
            at_a_site = conn.execute(
                text(
                    'SELECT count(*) FROM clinicq.user_roles r JOIN clinicq."user" u '
                    "ON u.id = r.user_id WHERE u.email LIKE '%@clinicq.example' "
                    "AND r.scope_type = 'site'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert users == len(DEMO_STAFF)
    # One unscoped assignment each, plus a site assignment for everyone but the platform admin,
    # who reaches a clinic only through the audited cross-site hatch (Issue 19).
    assert at_a_site == len(DEMO_STAFF) - 1
    assert assignments == len(DEMO_STAFF) + at_a_site


@pytest.mark.postgres
def test_a_rerun_repairs_a_changed_account_instead_of_adding_one(
    migrated_database: URL,
) -> None:
    """Someone demotes and deactivates a seeded account: the next run puts it back, in place."""
    url = migrated_database.render_as_string(hide_password=False)
    assert _run_seed(DATABASE_URL=url).returncode == 0
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE clinicq.\"user\" SET role = 'user', is_active = false "
                    "WHERE email = 'manager@clinicq.example'"
                )
            )
        rerun = _run_seed(DATABASE_URL=url)
        with engine.connect() as conn:
            role, active, count = conn.execute(
                text(
                    'SELECT role, is_active, (SELECT count(*) FROM clinicq."user" '
                    "WHERE email LIKE '%@clinicq.example') FROM clinicq.\"user\" "
                    "WHERE email = 'manager@clinicq.example'"
                )
            ).one()
    finally:
        engine.dispose()

    assert "Staff accounts: 0 created, 1 updated, 3 unchanged" in rerun.stdout
    assert (role, active, count) == ("clinic_manager", True, len(DEMO_STAFF))
