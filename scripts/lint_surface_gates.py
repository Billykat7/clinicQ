#!/usr/bin/env python3
"""Lint the RBAC gating of this app's *surfaces* — pages, mutating routes and controls.

**This reads template and router SOURCE. It never renders a page and never inspects a response
body.** That distinction is the whole point, and it is deliberate:
``docs/IDE/RULES/testing-strategy.mdc`` forbids tests that assert HTML body content, and this lint
does not — it parses `src/web/routes.py`'s AST and scans template text the way
``test_rbac_manifest_registry.py`` scans ``src/``. Please do not delete it as a rule violation; the
rule is about asserting on rendered output, which is a different thing.

Why it exists (Issue #170, M28). Every UI-layer gap the M28 second wave fixed was found the way the
M27 sweep found its own: a human read the templates and routes once, by hand. The guards that
already exist all point at *code shape* — no resource enum reappears, every ``require()`` call site
resolves against a manifest, each ``NavDestination``'s gate matches its console's dependency — and
none of them walks the **set of surfaces** asserting each one is gated. So a new console could ship
an ungated page, an ownership-only POST, or a Delete button whose route requires a verb the template
never checks, and nothing would go red.

Three checks, each with an explicit, commented allow-list so an exception is a decision on the
record rather than an omission:

1. **Page routes** — every ``@router.get`` returning HTML declares a gate.
2. **Mutating routes** — every ``@router.post|patch|put|delete`` consults a grant. An ownership
   lookup alone does not satisfy it (Issue #167's finding, made permanent).
3. **Template controls** — a state-changing control in an admin template sits inside a
   ``can(...)``/``can_action(...)`` block, or carries a ``data-can-*`` verdict (Issue #168).

Run it directly, or through ``tests/unit/security/test_surface_inventory_guard.py``::

    python -m scripts.lint_surface_gates          # exit 1 and a report on any finding
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WEB_ROUTES = _ROOT / "src" / "web" / "routes.py"
_TEMPLATES = _ROOT / "src" / "templates"

_MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

#: Calls that constitute a real authorization check inside a web handler.
_GATE_CALLS = frozenset(
    {
        "_page_gate_denied",  # verb + scope tier via NavVisibility.can_surface (Issue #167)
        "visible",
        "can_surface",
        "can",
        "can_action",
    }
)

#: Page routes reachable without a grant, each with the reason. A page not listed here and not
#: gated is a finding.
_UNGATED_PAGES: dict[str, str] = {
    "/": "the public front door — a visitor has no account",
    "/register-clinic": (
        "the public clinic-registration form (Issue 29): whoever fills it in has no account, and "
        "the submission it makes grants nothing — a clinic pending verification, invisible to "
        "every patient-facing surface, with no role or session attached"
    ),
    "/search": "public property search — anonymous by design",
    "/apply/{unit_id}": "public application form — the applicant has no account",
    "/account/profile": "self-service: identity from the session, never a client-supplied id",
    "/account/security": "self-service (see /account/profile)",
    "/account/notifications": "self-service (see /account/profile)",
    "/notifications/unsubscribe": "signed-token unsubscribe — no session to hold a grant",
    "/me/consent": (
        "a patient's own page (Issue 21): the patient session is the gate, the answers come from "
        "/api/v1/patients/me/consents behind require_patient, and without a session it renders "
        "the signed-out version rather than anything of theirs"
    ),
    "/invite": (
        "a staff invitation link (Issue 22): the person opening it has no account yet, so there "
        "is no grant to hold. The page is a shell — the token is checked by "
        "/api/v1/staff/invitations/preview, which answers the same way for a link that is used, "
        "revoked, expired or unknown"
    ),
    "/portal/documents/{document_id}": "ownership-scoped signed-link mint; the portal page above it is gated",
    "/jobs/{work_order_id}": "gated by `portal.jobs` inside the handler via _page_gate_denied",
    "/owner/properties/{property_id}": "owner-scoped drill-down; /owner is gated and this 403s a non-owner",
    "/owner/statements/{period}": "owner-scoped statement download (see above)",
    "/health": "liveness probe — must never depend on auth or the database",
    "/home": "public marketing page — anonymous by design",
    "/features": "public marketing page — anonymous by design",
    "/privacy": "public legal page — anonymous by design",
    "/terms": "public legal page — anonymous by design",
}

#: Mutating routes reachable without a grant, each with the reason (Issue #167's two exemptions).
_UNGATED_MUTATIONS: dict[str, str] = {
    "/apply/{unit_id}": "public application form — the caller is not signed in",
    "/notifications/unsubscribe": "signed-token unsubscribe — no session to hold a grant",
    "/me/consent": (
        "a patient's own page (Issue 21): the patient session is the gate, the answers come from "
        "/api/v1/patients/me/consents behind require_patient, and without a session it renders "
        "the signed-out version rather than anything of theirs"
    ),
}

#: Template controls deliberately left ungated, each with the reason (Issue #168's exception list).
_UNGATED_CONTROLS: dict[str, str] = {
    "admin/alerts.html:Mark read": "route requires only the READ this page already required",
    "admin/announcements.html:Mark read": "route requires only the READ this page already required",
    "admin/messages.html:Mark read": "route requires only the READ this page already required",
    "admin/messages.html:Send reply": "post_message is participant-scoped, not grant-gated",
    "admin/property_create.html:Create property": "its page route is gated on properties:create",
    "admin/lease_template_editor.html:Link": "a markdown-toolbar formatting button, not a mutation",
}

_CONTROL_LABEL = re.compile(
    r"\b(new|add|save|send|create|delete|remove|assign|approve|reject|complete|cancel|start|"
    r"restore|update|re-gate|reset|mark read|edit|activate|deactivate|link|unlink|terminate|"
    r"withdraw|accept|propose|reverse|record|publish|resend|sign)\b",
    re.I,
)
_LABEL_IS_NOT_A_MUTATION = re.compile(
    r"^(cancel|close|back[\w\s]*|export[\w\s]*|download|print|clear filters|view|open full|keep[\w\s]*)$",
    re.I,
)
_GATE_IN_JINJA = re.compile(r"\bcan\(|\bcan_action\(")


@dataclass(frozen=True, slots=True)
class Finding:
    """One ungated surface: where it is, what it is, and what would satisfy the lint."""

    kind: str
    where: str
    detail: str

    def __str__(self) -> str:
        """Render as one report line."""
        return f"[{self.kind}] {self.where}: {self.detail}"


def _called_names(node: ast.AST) -> set[str]:
    """Return every function and attribute name called anywhere inside ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def _routes(source: str) -> list[tuple[str, str, str, set[str]]]:
    """Return ``(method, path, handler, called_names)`` for every route in ``source``."""
    tree = ast.parse(source)
    found: list[tuple[str, str, str, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        calls = _called_names(node)
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            found.append((func.attr, str(decorator.args[0].value), node.name, calls))
    return found


def check_page_routes(source: str | None = None) -> list[Finding]:
    """Every HTML page route declares a gate, or is on the allow-list with a reason."""
    text = source if source is not None else _WEB_ROUTES.read_text(encoding="utf-8")
    findings: list[Finding] = []
    for method, path, handler, calls in _routes(text):
        if method != "get" or path in _UNGATED_PAGES:
            continue
        # A pure redirect helper has nothing to gate — it hands off to the page that does.
        if calls & _GATE_CALLS or "RedirectResponse" in calls:
            continue
        findings.append(
            Finding(
                "page",
                f"{handler} ({path})",
                "renders a page with no grant check — gate it, or add it to _UNGATED_PAGES "
                "with the reason it needs none",
            )
        )
    return findings


def check_mutating_routes(source: str | None = None) -> list[Finding]:
    """Every mutating route consults a grant; an ownership lookup alone does not count."""
    text = source if source is not None else _WEB_ROUTES.read_text(encoding="utf-8")
    findings: list[Finding] = []
    for method, path, handler, calls in _routes(text):
        if method not in _MUTATING_METHODS or path in _UNGATED_MUTATIONS:
            continue
        if calls & _GATE_CALLS:
            continue
        findings.append(
            Finding(
                "mutation",
                f"{handler} ({path})",
                "changes state with no grant check — ownership answers *whose* row, the grant "
                "answers *what*; add both, or list it in _UNGATED_MUTATIONS with the reason",
            )
        )
    return findings


def _jinja_gate_stack(lines: list[str], upto: int) -> list[bool]:
    """Return the ``{% if %}`` stack at line ``upto``; each entry says whether it is a grant check."""
    stack: list[bool] = []
    for line in lines[:upto]:
        for match in re.finditer(
            r"\{%-?\s*(if|elif|else|endif|for|endfor)\b([^%]*?)-?%\}", line
        ):
            keyword, rest = match.group(1), match.group(2)
            if keyword in ("if", "for"):
                stack.append(
                    bool(_GATE_IN_JINJA.search(rest)) if keyword == "if" else False
                )
            elif keyword == "elif" and stack:
                stack[-1] = bool(_GATE_IN_JINJA.search(rest))
            elif keyword == "else" and stack:
                stack[-1] = False
            elif keyword in ("endif", "endfor") and stack:
                stack.pop()
    return stack


def check_template_controls() -> list[Finding]:
    """Every state-changing control in an admin template sits inside a grant check."""
    findings: list[Finding] = []
    for template in sorted(_TEMPLATES.rglob("*.html")):
        rel = str(template.relative_to(_TEMPLATES))
        if rel != "rbac_admin.html" and not rel.startswith("admin/"):
            continue
        text = template.read_text(encoding="utf-8")
        lines = text.splitlines()
        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", text, re.S | re.I):
            label = " ".join(
                re.sub(r"<[^>]+>|\{[{%].*?[%}]\}", " ", match.group(1)).split()
            )
            attrs = match.group(0)[: match.group(0).index(">") + 1]
            if (
                not label
                or _LABEL_IS_NOT_A_MUTATION.match(label)
                or not _CONTROL_LABEL.search(label)
            ):
                continue
            if f"{rel}:{label}" in _UNGATED_CONTROLS or "data-can-" in attrs:
                continue
            line_no = text[: match.start()].count("\n")
            if any(_jinja_gate_stack(lines, line_no)):
                continue
            findings.append(
                Finding(
                    "control",
                    f"{rel}:{line_no + 1}",
                    f'"{label[:40]}" is state-changing but carries no can(...) gate — gate it on '
                    f"the verb its route enforces, or list it in _UNGATED_CONTROLS with the reason",
                )
            )
    return findings


def run() -> list[Finding]:
    """Run all three checks and return every finding."""
    return [*check_page_routes(), *check_mutating_routes(), *check_template_controls()]


def main() -> int:
    """Print the report; return the process exit code."""
    findings = run()
    if not findings:
        print("surface gates: clean")
        return 0
    print(f"surface gates: {len(findings)} ungated surface(s)", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
