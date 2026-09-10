#!/usr/bin/env python3
"""Seed a verified BK ClinicQ user in the configured PostgreSQL schema (local/dev bootstrap).

Creates the user in ``clinicq.user`` (or ``DB_SCHEMA``) via the normal ORM session.
Idempotent: skips when the email already exists.

Usage (from project root, venv active, PYTHONPATH set):

    python scripts/db/seed-dev-user.py --email admin@btk.com --password 'secret'
    DEFAULT_USER_PASSWORD=secret python scripts/db/seed-dev-user.py --email admin@btk.com
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import select

from src.commons.enums import UserRole
from src.core.config import get_settings
from src.core.security import hash_password
from src.database.models import User
from src.database.session import get_db_context


def _load_dotenv_value(key: str) -> str:
    """Return a single key from project ``.env`` when not already in the process env."""
    if os.environ.get(key):
        return os.environ[key]
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return ""
    prefix = f"{key}="
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def _parse_args() -> argparse.Namespace:
    """Parse CLI flags for the bootstrap user."""
    parser = argparse.ArgumentParser(
        description="Seed a verified user in the BK ClinicQ PostgreSQL schema.",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("SEED_USER_EMAIL", "admin@btk.com"),
        help="User email (default: SEED_USER_EMAIL or admin@btk.com).",
    )
    parser.add_argument(
        "--password",
        default=_load_dotenv_value("DEFAULT_USER_PASSWORD"),
        help="Plain password (default: DEFAULT_USER_PASSWORD env or .env).",
    )
    parser.add_argument(
        "--role",
        choices=[UserRole.USER.value, UserRole.ADMIN.value],
        default=UserRole.ADMIN.value,
        help="RBAC role stored on user.role (default: admin).",
    )
    parser.add_argument(
        "--first-name",
        default="Admin",
        help="Optional first name.",
    )
    parser.add_argument(
        "--last-name",
        default="User",
        help="Optional last name.",
    )
    return parser.parse_args()


def seed_dev_user(
    *,
    email: str,
    password: str,
    role: str = UserRole.ADMIN.value,
    first_name: str | None = None,
    last_name: str | None = None,
) -> User:
    """Insert or return an existing verified user in the application schema."""
    normalized = email.lower().strip()
    if not normalized:
        raise ValueError("email is required")
    if not password:
        raise ValueError(
            "password is required (pass --password or set DEFAULT_USER_PASSWORD)"
        )

    settings = get_settings()
    with get_db_context() as db:
        existing = db.execute(
            select(User).where(User.email == normalized)
        ).scalar_one_or_none()
        if existing is not None:
            print(
                f"User already exists in schema {settings.db_schema.value}: {normalized}"
            )
            return existing

        user = User(
            email=normalized,
            password=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            is_verified=True,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(
            f"Created {role} user in schema {settings.db_schema.value}: {normalized} (id={user.id})"
        )
        return user


def main() -> int:
    """CLI entrypoint."""
    args = _parse_args()
    try:
        seed_dev_user(
            email=args.email,
            password=args.password,
            role=args.role,
            first_name=args.first_name,
            last_name=args.last_name,
        )
    except Exception as exc:
        # Broad on purpose: a bootstrap script reports any failure to the shell as exit 1.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
