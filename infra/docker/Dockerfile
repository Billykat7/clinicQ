# BK ClinicQ app image. Build context is repo root (requirements.txt, src/);
# see the root .dockerignore for what is kept out of that context.
# From repo root: docker build -f infra/docker/Dockerfile -t clinicq-app:local .

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies (upgrade first so base image gets security patches).
# Explicit openssl/libssl3t64 so apt resolves to current security revisions (e.g. CVE fixes on Debian).
RUN apt-get update && apt-get full-upgrade -y && apt-get install -y \
    build-essential \
    libpq-dev \
    openssl \
    libssl3t64 \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade setuptools/wheel so vendored deps are not vulnerable (CVE-2026-23949, CVE-2026-24049)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade "setuptools>=81" "wheel>=0.46.2"

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Drop dev/test/lint/audit tooling from the venv before it ships to the runtime stage.
# The container never runs pytest/ruff/mypy/black/isort/flake8/pre-commit/pip-audit — CI
# installs the full requirements.txt separately — so pruning here shrinks the image without
# changing requirements.txt or any CI step (verified: nothing under src/ imports these).
# The loop tolerates packages a given service does not have, so one list serves every sibling.
RUN for pkg in \
      pytest pytest-asyncio pytest-cov pytest-xdist hypothesis coverage pluggy iniconfig execnet \
      black isort flake8 pycodestyle pyflakes mccabe mypy ruff pytokens \
      pre-commit nodeenv identify cfgv virtualenv distlib \
      pip-audit pip-api pip-requirements-parser cyclonedx-python-lib license-expression \
      boolean.py py-serializable packageurl-python cachecontrol tomli-w \
      pandas-stubs types-PyYAML types-qrcode types-pytz types-reportlab; do \
        pip uninstall -y "$pkg" >/dev/null 2>&1 || true; \
    done

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies (upgrade first so base image gets security patches).
# Explicit openssl/libssl3t64/libnghttp2-14 to keep security-sensitive transitive libs current.
RUN apt-get update && apt-get full-upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libpq5 \
    openssl \
    libssl3t64 \
    libnghttp2-14 \
    && rm -rf /var/lib/apt/lists/*

# App user with home — matplotlib and others use ~/.config; without -m, /home/appuser is missing.
RUN groupadd -r appuser && useradd -r -m -g appuser appuser

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Upgrade system Python's setuptools/wheel so Trivy does not report vulns in base image (CVE-2026-23949, CVE-2026-24049)
RUN /usr/local/bin/python3 -m pip install --no-cache-dir --upgrade "setuptools>=81" "wheel>=0.46.2"

# Remove pip from both the venv and system Python. The app never invokes pip at
# runtime, but pip bundles its own internal copies of old setuptools and msgpack
# under pip/_vendor/ (e.g. setuptools==70.3.0, msgpack==1.1.2) that Trivy flags as
# HIGH CVEs regardless of the setuptools/wheel versions installed above.
RUN rm -rf /opt/venv/lib/python3.*/site-packages/pip \
           /opt/venv/lib/python3.*/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip* \
           /usr/local/lib/python3.*/site-packages/pip \
           /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
           /usr/local/bin/pip*

# Strip Python bytecode caches from the shipped venv. PYTHONDONTWRITEBYTECODE keeps the
# server from regenerating them, so on disk they are pure image weight.
RUN find /opt/venv -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /opt/venv -name '*.pyc' -delete

# Copy application code (includes src/static for /static mount)
COPY src/ ./src/

# Alembic ships in-image for operator-run migrations (not CD, not container start).
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# Writable matplotlib config (avoids slow temp dir fallback if ~/.config is ever tight)
ENV MPLCONFIGDIR=/tmp/mplconfig
RUN mkdir -p /tmp/mplconfig && chown appuser:appuser /tmp/mplconfig

# Change ownership
RUN chown -R appuser:appuser /app
USER appuser

# Liveness only: /health/live never touches the database or S3, so a dependency
# outage does not make the container unhealthy and restart-loop (readiness is polled
# externally against /health/ready; /health is the uniform liveness heartbeat —
# see docs/CICD/UPTIME-MONITORING.md).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Expose port
EXPOSE 8000

# --proxy-headers for X-Forwarded-* / scheme; --forwarded-allow-ips so the
# immediate peer on typical Docker networks (nginx → app) is trusted for forwarded
# headers (default uvicorn trust list is 127.0.0.1 only, which breaks container-to-container).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8", "--timeout-graceful-shutdown", "25"]
