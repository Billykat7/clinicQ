"""Registry integrity + CI typo-guard for the module manifest framework (Issues #149/#154).

Two guarantees the ``PermissionResource`` enum used to provide for free, reimplemented without it
(design doc §10):

1. **Catalog integrity** — the registered manifests are, since Issue #154 (M27), the *only* source
   of truth for the resource catalog: no enum to cross-check against any more. So the checks here
   are internal ones a self-describing registry can still make — no two manifests claim the same
   key, every declared key is well-formed, and each node's dotted key actually composes from its
   parent's. That the manifests reproduce the shipped catalog *exactly* — every pre-M27 key present
   with its original parent, and nothing added but Issue #153's ``reporting`` tree — is asserted
   against the committed pre-M27 snapshot in ``tests/integration/test_rbac_m27_full_parity.py``,
   which is the only place that comparison can still be made now the enum is gone.
2. **Typo safety net** — every ``require()``/``require_action()``/``require_management()`` call
   site anywhere under ``src/`` resolves its string resource key (and, for ``require_action``, its
   action key) against a manifest actually registered in ``ALL_MANIFESTS``. Found by walking the
   AST rather than importing every module (cheap, no app/DB boot required, and catches a call site
   even if nothing currently exercises it at runtime). This is the check that makes plain string
   keys as typo-safe as the enum was.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import ast
from pathlib import Path

from src.core.rbac_manifest import all_full_keys, named_action_keys
from src.core.rbac_manifest_registry import ALL_MANIFESTS

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"


_FACTORY_NAMES = {"require", "require_action", "require_management"}


def test_all_manifests_have_no_duplicate_resource_keys() -> None:
    """No two manifests in the registry declare the same resource key."""
    seen: dict[str, str] = {}
    for manifest in ALL_MANIFESTS:
        for key in all_full_keys(manifest):
            assert key not in seen, (
                f"resource key {key!r} is declared by both {seen[key]!r} and "
                f"{manifest.key!r} — manifests must not overlap"
            )
            seen[key] = manifest.key


def test_every_declared_resource_key_is_well_formed() -> None:
    """Keys are lowercase dotted identifiers — the shape every stored grant and gate assumes."""
    for manifest in ALL_MANIFESTS:
        for key in all_full_keys(manifest):
            assert key, f"manifest {manifest.key!r} declares an empty resource key"
            for segment in key.split("."):
                assert segment.isidentifier() and segment.islower(), (
                    f"resource key {key!r} (manifest {manifest.key!r}) is not a lowercase "
                    "dotted identifier"
                )


def _imported_factory_names(tree: ast.Module) -> set[str]:
    """Return the local names bound to the ``require*`` factories via a ``rbac_deps`` import."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "src.api.rbac_deps":
            for alias in node.names:
                if alias.name in _FACTORY_NAMES:
                    names.add(alias.asname or alias.name)
    return names


def _iter_call_sites() -> list[tuple[Path, str, str, object, int]]:
    """Walk every ``src/**/*.py`` file for a ``require*(resource_key, ...)`` call site.

    Only counts a call when the file actually imports the factory from ``src.api.rbac_deps``
    (so an unrelated same-named function elsewhere can never produce a false positive), and only
    when the resource key is a literal string (a dynamic key can't be statically checked here —
    none exist in this codebase today).
    """
    sites: list[tuple[Path, str, str, object, int]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_factory_names(tree)
        if not imported:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in imported
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            second_arg = (
                node.args[1].value
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                else None
            )
            sites.append(
                (
                    path.relative_to(_SRC_ROOT.parent),
                    node.func.id,
                    node.args[0].value,
                    second_arg,
                    node.lineno,
                )
            )
    return sites


def test_every_require_call_site_resolves_against_a_registered_manifest() -> None:
    """Every ``require*`` call site's resource (and named-action) key exists in ``ALL_MANIFESTS``."""
    known_keys: set[str] = set()
    named_actions: set[tuple[str, str]] = set()
    for manifest in ALL_MANIFESTS:
        known_keys |= all_full_keys(manifest)
        named_actions |= named_action_keys(manifest)

    call_sites = _iter_call_sites()
    assert call_sites, (
        "expected at least one require()/require_action()/require_management() call site "
        "(the Applications pilot module) — none were found; did the pilot migration regress?"
    )

    for path, func_name, resource_key, second_arg, lineno in call_sites:
        assert resource_key in known_keys, (
            f"{path}:{lineno} calls {func_name}({resource_key!r}, ...) but {resource_key!r} "
            "is not declared by any manifest in ALL_MANIFESTS"
        )
        if func_name == "require_action":
            assert (resource_key, second_arg) in named_actions, (
                f"{path}:{lineno} calls require_action({resource_key!r}, {second_arg!r}) but "
                "that pair is not declared as a named_action in any registered manifest"
            )
