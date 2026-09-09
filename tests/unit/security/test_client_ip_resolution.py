"""There is exactly one client-IP resolver, and it reads the right end of the header (Issue #179).

Two claims, both load-bearing:

1. **Only one resolver exists.** Five modules used to define their own `_client_ip`, and the copies
   disagreed — the one keying both per-IP limiters returned the socket peer, which behind nginx is
   nginx. The last test in this file AST-walks `src/` so the auth router cannot quietly grow its own
   again; a comment cannot enforce that, and a code review evidently did not.

2. **It reads `X-Forwarded-For` from the right.** The header is append-only and client-supplied at
   the *left*: our nginx uses `$proxy_add_x_forwarded_for`, which appends the address it actually
   saw. So a forged `X-Forwarded-For: 1.2.3.4` arrives at the app as `1.2.3.4, <real client>`, and
   reading `parts[0]` hands the attacker a free limiter bucket per forged value — unlimited buckets,
   so no budget ever fills. The pre-M30 logging resolver did exactly that.

Unit-level rather than integration because the resolver is a pure function of a request and the
settings, and the property under test is arithmetic on a header — the integration counterpart (the
limiter actually bucketing two proxied clients apart) lives in
`tests/integration/security/test_rate_limit_backends.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from src.commons.enums import AppEnvironment
from src.core.client_ip import client_ip_or_unknown, resolve_client_ip
from src.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

#: The one module allowed to define a client-IP resolver.
CANONICAL_RESOLVER_MODULE = SRC_ROOT / "core" / "client_ip.py"

_PEER = "198.51.100.9"


def _settings(*, trust: bool, hops: int = 1) -> Settings:
    """Isolated settings (no ``.env``) with the proxy-trust knobs under test set explicitly."""
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="client-ip-resolution-test-secret-32chars!",
        trust_proxy_headers=trust,
        trusted_proxy_hops=hops,
    )


def _request(forwarded: str | None = None, *, peer: str | None = _PEER) -> object:
    """A minimal request stand-in: the resolver reads only ``headers`` and ``client``."""
    headers = Headers({"x-forwarded-for": forwarded} if forwarded is not None else {})
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(headers=headers, client=client)


# --- trust off: the header is inert ------------------------------------------------------------


def test_a_forged_header_cannot_move_the_key_when_trust_is_off() -> None:
    """The default posture: `X-Forwarded-For` is attacker-controlled data and is ignored."""
    request = _request("1.2.3.4")
    assert resolve_client_ip(request, _settings(trust=False)) == _PEER  # type: ignore[arg-type]


def test_trust_is_off_by_default() -> None:
    """Asserted on ``Settings`` itself: a deployment must opt in, never inherit proxy trust."""
    defaults = Settings(_env_file=None, jwt_secret="a" * 40)  # type: ignore[arg-type]
    assert defaults.trust_proxy_headers is False


# --- trust on: the right element, from the right end -------------------------------------------


def test_a_single_hop_reads_the_element_nginx_appended() -> None:
    """One trusted proxy: the client is the **last** element, not the first."""
    request = _request("1.2.3.4, 203.0.113.7")
    assert resolve_client_ip(request, _settings(trust=True, hops=1)) == "203.0.113.7"


def test_the_forged_prefix_is_never_returned() -> None:
    """The spoofing case stated directly: what the attacker wrote must not become the key."""
    forged = "evil-bucket-1"
    request = _request(f"{forged}, 203.0.113.7")
    assert resolve_client_ip(request, _settings(trust=True, hops=1)) != forged


def test_two_hops_read_past_the_inner_proxy() -> None:
    """A trusted CDN in front of nginx: the last element is nginx's view *of the CDN*."""
    request = _request("203.0.113.7, 70.132.1.1")
    assert resolve_client_ip(request, _settings(trust=True, hops=2)) == "203.0.113.7"


def test_a_header_shorter_than_the_configured_depth_falls_back_to_the_peer() -> None:
    """Fail closed: a header that is not shaped as configured is not believed."""
    request = _request("203.0.113.7")
    assert resolve_client_ip(request, _settings(trust=True, hops=2)) == _PEER


@pytest.mark.parametrize("header", ["", "   ", " , ,  "])
def test_an_empty_or_whitespace_header_falls_back_to_the_peer(header: str) -> None:
    """An empty header carries no address; the socket does."""
    request = _request(header)
    assert resolve_client_ip(request, _settings(trust=True)) == _PEER


def test_surrounding_whitespace_is_stripped() -> None:
    """`X-Forwarded-For` is comma-*and-space* separated; a key must not carry the space."""
    request = _request("1.2.3.4,   203.0.113.7  ")
    assert resolve_client_ip(request, _settings(trust=True)) == "203.0.113.7"


# --- no address at all --------------------------------------------------------------------------


def test_no_client_resolves_to_none() -> None:
    """An ASGI transport with no client (some harnesses) has no address to report."""
    assert resolve_client_ip(_request(peer=None), _settings(trust=False)) is None  # type: ignore[arg-type]


def test_the_unknown_placeholder_is_a_named_bucket() -> None:
    """Limiter keys are strings; the unaddressable bucket is explicit rather than ``str(None)``."""
    assert (
        client_ip_or_unknown(_request(peer=None), _settings(trust=False)) == "unknown"
    )  # type: ignore[arg-type]


# --- the standing guard --------------------------------------------------------------------------


def _resolver_definitions() -> list[str]:
    """Return ``path:function`` for every client-IP-shaped resolver defined outside the canonical one.

    Matched on the *shape* of the name rather than an exact string, because the five copies this
    issue deleted were not identically named and the next one would not be either.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == CANONICAL_RESOLVER_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            name = node.name.lstrip("_").lower()
            if "client_ip" in name and not name.startswith("get_client_ip"):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
    return offenders


def test_no_module_defines_a_second_client_ip_resolver() -> None:
    """The acceptance criterion, enforced rather than asserted in a comment.

    ``get_client_ip`` in ``request_logging`` is exempt by name: it reads the request-scoped
    contextvar the middleware already populated *from* the canonical resolver, so it is a lookup,
    not a second implementation.
    """
    assert _resolver_definitions() == [], (
        "A second client-IP resolver has appeared. There must be exactly one, in "
        "src/core/client_ip.py — five divergent copies is how the per-IP rate limiters ended up "
        "keyed on the reverse proxy's own address (pen-test F-04)."
    )
