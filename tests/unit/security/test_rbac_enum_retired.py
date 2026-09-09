"""Permanent anti-regression guard: the RBAC resource enum stays retired (Issue #154, M27).

``PermissionResource`` and ``PERMISSION_RESOURCE_PARENT`` were a shared, hand-maintained chokepoint
every module had to edit to add a resource, and the pair of them was deleted for good in M27. This
file is the standing rule that keeps them deleted — it runs on every PR, not once at merge time.
Resources are plain string keys, permanently, declared by each module in its own
``rbac_manifest.py`` (``docs/architecture/rbac-module-self-registration.md`` §7).

It guards the **shape**, not just the names, because the failure mode this milestone exists to
prevent is a developer reintroducing the same chokepoint under a different label — a ``StrEnum``
called something else, a "generated constants" module, or a fresh crop of
``require_<resource>_<verb>`` dependency functions. Four rules:

1. Neither deleted name is defined, imported or referenced in executable code anywhere under
   ``src/`` (prose in a docstring or comment is fine — the history is worth keeping).
2. No ``Enum``/``StrEnum`` under ``src/`` enumerates resource keys: conclusive on a single dotted
   key (``lease.details`` is unmistakably an RBAC resource), with a coarser three-undotted-keys
   backstop that carries a small, deliberately awkward allowlist.
3. No module-level constant under ``src/`` enumerates a large set of resource keys — the
   generated-constants module shape — outside the manifest layer that is *supposed* to.
4. ``src/api/rbac_deps.py`` defines only the three generic factories, and nothing anywhere is named
   like a per-``(resource, verb)`` dependency function.

**What this cannot catch, by construction.** A guard that keys on names and shapes cannot recognise
a *semantic* reintroduction. ``src/modules/documents/enums.py``'s ``OWNER_TYPE_RESOURCE`` is the
pattern to watch: a dict mapping four document-owner kinds to the resource that governs each, read
at request time by ``documents/router.py``'s one ``ensure_permission_key`` call. That is
dynamic dispatch — the right idea, and it stays — but a future dict of the same shape, imported
under any alias and populated with a locally invented vocabulary instead of manifest-declared keys,
would slip past every rule below. Reviewers, not this file, are the check on that; see the note in
``documents/enums.py`` itself.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import ast
from pathlib import Path

from src.core.rbac_manifest_registry import manifest_resource_keys

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"


_RETIRED_NAMES = {"PermissionResource", "PERMISSION_RESOURCE_PARENT"}


_ENUM_BASES = {"Enum", "StrEnum", "IntEnum", "IntFlag", "Flag"}


# An enum whose members happen to overlap the resource vocabulary without being one. Adding to this
# set is meant to be uncomfortable: it is a shared file, it needs a written justification here, and
# a reviewer sees it in the diff — precisely the friction the enum used to lack.
#
# ``BoundedContext`` (``src/commons/enums.py``) names one entry per ``src/modules`` package for the
# OpenAPI contract split. Eight of its fifteen members share a spelling with a resource key because
# a module and its top-level resource are naturally named the same thing; nothing reads it as an
# RBAC resource, and it long predates the catalog.
_ALLOWED_OVERLAPPING_ENUMS = frozenset({"BoundedContext"})


# Below this many undotted resource-key members, an overlap is coincidence (``DbSchema`` is
# ``properties``; ``UnitStatus`` has ``maintenance``). A single *dotted* key trips regardless.
_UNDOTTED_OVERLAP_LIMIT = 3


# The manifest layer is allowed — required, in fact — to enumerate resource keys.
_MANIFEST_PATHS = (
    "rbac_manifest.py",
    "rbac_manifest_registry.py",
    "rbac_manifest_sync.py",
)


# A constant naming more keys than this is a catalog, not a grouping. ``SHELL_RESOURCES``
# (``src/core/rbac.py``), the largest legitimate one, names three.
_CONSTANT_KEY_LIMIT = 8


_ALLOWED_RBAC_DEPS_FUNCTIONS = {"require", "require_action", "require_management"}


_VERB_AND_ACTION_SUFFIXES = (
    "read",
    "create",
    "update",
    "delete",
    "sign",
    "approve",
    "reject",
    "send",
)


def _python_files() -> list[Path]:
    """Every ``src/**/*.py`` file, ``__pycache__`` excluded."""
    return [
        path
        for path in sorted(_SRC_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _parsed() -> list[tuple[Path, ast.Module]]:
    """Every source file paired with its AST (parsed once, reused by each rule)."""
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in _python_files()
    ]


def _rel(path: Path) -> str:
    """Repo-relative path for an assertion message."""
    return str(path.relative_to(_SRC_ROOT.parent))


def _string_members(node: ast.ClassDef) -> list[str]:
    """Return the string values assigned in a class body (an enum's member values)."""
    values: list[str] = []
    for statement in node.body:
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        value = (
            statement.value
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            else None
        )
        if targets and isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)
    return values


def _string_constants(node: ast.AST) -> list[str]:
    """Return every string literal directly inside a list/tuple/set/dict display."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elements: list[ast.expr] = list(node.elts)
    elif isinstance(node, ast.Dict):
        elements = [key for key in node.keys if key is not None]
    elif isinstance(node, ast.Call):
        # ``frozenset({...})`` / ``set([...])`` and friends.
        elements = []
        for arg in node.args:
            return _string_constants(arg)
    else:
        return []
    return [
        e.value
        for e in elements
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]


def test_the_retired_names_are_not_defined_or_imported_anywhere_in_src() -> None:
    """``PermissionResource``/``PERMISSION_RESOURCE_PARENT`` exist nowhere but in prose."""
    offences: list[str] = []
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in _RETIRED_NAMES:
                offences.append(f"{_rel(path)}:{node.lineno} defines class {node.name}")
            elif isinstance(node, ast.Name) and node.id in _RETIRED_NAMES:
                offences.append(f"{_rel(path)}:{node.lineno} references {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in _RETIRED_NAMES:
                offences.append(f"{_rel(path)}:{node.lineno} references .{node.attr}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name in _RETIRED_NAMES:
                        offences.append(
                            f"{_rel(path)}:{node.lineno} imports {alias.name}"
                        )
    assert not offences, (
        "PermissionResource/PERMISSION_RESOURCE_PARENT were permanently deleted in Issue #154 "
        "and must not come back. Declare resources in the module's own rbac_manifest.py and use "
        "plain string keys.\n  " + "\n  ".join(offences)
    )


def _offending_enums(label: str, tree: ast.Module, keys: set[str]) -> list[str]:
    """Return one message per enum in ``tree`` that enumerates RBAC resource keys.

    Split out from the test so :func:`test_the_guard_itself_detects_a_reintroduced_enum` can prove
    the detector fires — a guard nothing has ever tripped is a guard nobody knows works.
    """
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        bases |= {b.attr for b in node.bases if isinstance(b, ast.Attribute)}
        if not bases & _ENUM_BASES:
            continue
        members = _string_members(node)
        dotted = [v for v in members if "." in v and v in keys]
        undotted = [v for v in members if "." not in v and v in keys]
        if dotted:
            offences.append(
                f"{label}:{node.lineno} enum {node.name} has dotted resource "
                f"key(s) {sorted(dotted)}"
            )
        elif (
            len(undotted) >= _UNDOTTED_OVERLAP_LIMIT
            and node.name not in _ALLOWED_OVERLAPPING_ENUMS
        ):
            offences.append(
                f"{label}:{node.lineno} enum {node.name} enumerates "
                f"{len(undotted)} resource keys {sorted(undotted)}"
            )
    return offences


def test_no_enum_under_src_enumerates_rbac_resource_keys() -> None:
    """A resource-key enum is the retired chokepoint whatever it is called."""
    keys = set(manifest_resource_keys())
    offences = [
        offence
        for path, tree in _parsed()
        for offence in _offending_enums(_rel(path), tree, keys)
    ]
    assert not offences, (
        "An Enum/StrEnum under src/ enumerates RBAC resource keys — that is PermissionResource "
        "under a new name, and Issue #154 retired the pattern permanently. Declare the resources "
        "in a module manifest instead.\n  " + "\n  ".join(offences)
    )


def test_no_module_level_constant_enumerates_the_resource_catalog() -> None:
    """A big literal collection of resource keys is a generated-constants module by another name."""
    keys = set(manifest_resource_keys())
    offences: list[str] = []
    for path, tree in _parsed():
        if path.name in _MANIFEST_PATHS:
            continue
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            if statement.value is None:
                continue
            found = [v for v in _string_constants(statement.value) if v in keys]
            if len(found) > _CONSTANT_KEY_LIMIT:
                offences.append(
                    f"{_rel(path)}:{statement.lineno} enumerates {len(found)} resource keys"
                )
    assert not offences, (
        "A module-level constant under src/ enumerates the resource catalog. The catalog's source "
        "of truth is the registered module manifests (Issue #154) — derive from ALL_MANIFESTS "
        "rather than restating the keys.\n  " + "\n  ".join(offences)
    )


def test_rbac_deps_defines_only_the_three_generic_factories() -> None:
    """``src/api/rbac_deps.py`` is generic-only; a named dependency per resource never returns."""
    tree = ast.parse((_SRC_ROOT / "api" / "rbac_deps.py").read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert defined == _ALLOWED_RBAC_DEPS_FUNCTIONS, (
        "src/api/rbac_deps.py must define exactly require/require_action/require_management "
        f"(Issue #154). Found: {sorted(defined)}"
    )


def test_nothing_under_src_is_named_like_a_per_resource_dependency() -> None:
    """No ``require_<resource>_<verb>`` function anywhere — the shape, not just the old file."""
    offences: list[str] = []
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name in _ALLOWED_RBAC_DEPS_FUNCTIONS or not name.startswith("require_"):
                continue
            if name.rsplit("_", 1)[-1] in _VERB_AND_ACTION_SUFFIXES:
                offences.append(f"{_rel(path)}:{node.lineno} defines {name}")
    assert not offences, (
        "A per-(resource, verb) dependency function reappeared. Gate the route with "
        "Depends(require('resource.key', 'verb')) against a manifest-declared resource "
        "instead (Issue #154).\n  " + "\n  ".join(offences)
    )
