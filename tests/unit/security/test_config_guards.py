"""The configuration a deployment must not run with, refused in one pass (Issue 12).

``Settings.configuration_problems`` is the one list of rules: the boot guard raises with all of
them at once, and ``scripts/check_config.py`` prints the same list for an env file. These tests pin
the rules this issue added (debug mode, open CORS, DB_* parts that contradict DATABASE_URL), the
"all at once" behaviour both callers promise, and that neither ever prints a value.

Every Settings here is built with ``_env_file=None`` so a developer's ``.env`` cannot change the
outcome (``.cursor/rules/testing-strategy.mdc``).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.check_config import main as check_config
from src.commons.enums import AppEnvironment
from src.core.config import CORS_ANY_ORIGIN, Settings

_SECRET = "a-production-secret-that-is-long-enough-32"
_NOT_DEVELOPMENT = [AppEnvironment.STAGING, AppEnvironment.PRODUCTION]


def _settings(**values: object) -> Settings:
    """Settings from ``values`` alone, isolated from the local .env."""
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", _NOT_DEVELOPMENT)
def test_debug_mode_cannot_be_enabled_outside_development(
    environment: AppEnvironment,
) -> None:
    """The acceptance criterion, in both environments it covers."""
    with pytest.raises(
        ValidationError, match="DEBUG must be false outside development"
    ):
        _settings(environment=environment, jwt_secret=_SECRET, debug=True)


@pytest.mark.parametrize("environment", _NOT_DEVELOPMENT)
def test_cors_open_to_every_origin_is_refused_outside_development(
    environment: AppEnvironment,
) -> None:
    """``*`` lets any website drive the app from a visitor's browser."""
    with pytest.raises(ValidationError, match="CORS_ORIGINS must list exact origins"):
        _settings(
            environment=environment, jwt_secret=_SECRET, cors_origins=CORS_ANY_ORIGIN
        )


def test_development_may_debug_and_open_cors_and_production_may_name_origins() -> None:
    """The rules bite outside development only, and exact origins are always fine."""
    assert _settings(debug=True, cors_origins=CORS_ANY_ORIGIN).debug
    production = _settings(
        environment=AppEnvironment.PRODUCTION,
        jwt_secret=_SECRET,
        cors_origins="https://clinicq.example.org/, https://staff.clinicq.example.org",
    )
    assert production.cors_origins == [
        "https://clinicq.example.org",
        "https://staff.clinicq.example.org",
    ]


def test_every_problem_is_reported_in_one_error() -> None:
    """A deploy with four mistakes fails once, naming all four, not the first of them."""
    with pytest.raises(ValidationError) as caught:
        _settings(
            environment=AppEnvironment.PRODUCTION,
            debug=True,
            cors_origins=CORS_ANY_ORIGIN,
            bcrypt_rounds=4,
        )
    message = str(caught.value)
    for setting in ("JWT_SECRET", "BCRYPT_ROUNDS", "DEBUG", "CORS_ORIGINS"):
        assert setting in message


def test_db_parts_alone_build_the_database_url() -> None:
    """Before Issue 12, DB_HOST and DB_NAME without DATABASE_URL were ignored for localhost."""
    settings = _settings(
        db_host="db.internal", db_name="clinicq", db_user="clinicq", db_password="p@ss"
    )
    assert settings.database_url == "postgresql://clinicq:p%40ss@db.internal/clinicq"


@pytest.mark.parametrize("environment", _NOT_DEVELOPMENT)
def test_db_parts_contradicting_database_url_are_refused_outside_development(
    environment: AppEnvironment,
) -> None:
    """DATABASE_URL wins, so a DB_HOST naming another server is a mistake someone will act on."""
    with pytest.raises(ValidationError, match="DB_HOST disagrees with DATABASE_URL"):
        _settings(
            environment=environment,
            jwt_secret=_SECRET,
            database_url="postgresql://clinicq@db.internal/clinicq",
            db_host="old-db.internal",
        )


def test_check_config_lists_every_problem_in_one_pass_without_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field errors and environment rules together, one run, and no secret echoed back."""
    password = "hunter2-not-a-real-password"
    env_file = tmp_path / "broken.env"
    env_file.write_text(
        "\n".join(
            [
                "ENVIRONMENT=staging",
                "DEBUG=true",
                f"CORS_ORIGINS={CORS_ANY_ORIGIN}",
                "BCRYPT_ROUNDS=99",
                "OTP_LENGTH=six",
                "STRIPE_SECRET_KEY=sk_live_not_a_real_key",
                f"DATABASE_URL=postgresql://clinicq:{password}@db.internal/clinicq",
                "DB_HOST=old-db.internal",
                "IMAGE=ghcr.io/example/clinicq:v0.2.0",
            ]
        ),
        encoding="utf-8",
    )

    assert check_config([str(env_file)]) == 1
    out = capsys.readouterr().out
    for setting in (
        "BCRYPT_ROUNDS",
        "OTP_LENGTH",
        "STRIPE_SECRET_KEY",
        "JWT_SECRET",
        "DEBUG",
        "CORS_ORIGINS",
        "DB_HOST",
    ):
        assert f"✗ {setting}:" in out, setting
    assert "IMAGE" in out and "7 problem(s)" in out
    assert password not in out and "sk_live_not_a_real_key" not in out


def test_check_config_ignores_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report describes the file: a variable exported in the shell does not rescue it."""
    monkeypatch.setenv("JWT_SECRET", _SECRET)
    env_file = tmp_path / "prod.env"
    env_file.write_text("ENVIRONMENT=production\n", encoding="utf-8")

    assert check_config([str(env_file)]) == 1
    assert "✗ JWT_SECRET:" in capsys.readouterr().out


def test_check_config_exits_2_for_a_missing_file(tmp_path: Path) -> None:
    """A typo in the path is not a clean bill of health."""
    assert check_config([str(tmp_path / "missing.env")]) == 2
