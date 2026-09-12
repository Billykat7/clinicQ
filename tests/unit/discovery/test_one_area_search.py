"""One place-name search, for every channel (Issue 34).

USSD cannot use GPS, so the area search is how a feature-phone patient finds a clinic at all, and the
web page, the USSD menu and the WhatsApp bot must all find the **same** places for the same text. The
way that stays true is that there is one implementation: this walks the source and fails if any
module other than :mod:`src.modules.discovery.areas` reads the ``area_name`` table (by model or by
table name), or if the discovery routes stop calling it.

It reads source, not rendered output (``.cursor/rules/testing-strategy.mdc``), and the second test
proves it can fail.
"""

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
#: The one module allowed to query place names, and the model file that declares the table.
_ALLOWED = {
    _SRC / "modules" / "discovery" / "areas.py",
    _SRC / "database" / "models" / "area.py",
    _SRC / "database" / "models" / "__init__.py",
}


#: Raw SQL reading the table: ``FROM area_name``, ``JOIN clinicq.area_name`` and the like. Prose
#: that merely mentions the column (a docstring) is not a query.
_RAW_SQL = re.compile(r"\b(from|join)\s+(\w+\.)?area_name\b", re.IGNORECASE)


def _mentions_area_names(source: str) -> list[int]:
    """Lines naming the ``AreaName`` model, or raw SQL reading the ``area_name`` table."""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "AreaName":
            lines.append(node.lineno)
        elif isinstance(node, ast.alias) and node.name == "AreaName":
            lines.append(getattr(node, "lineno", 0))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _RAW_SQL.search(node.value)
        ):
            lines.append(node.lineno)
    return lines


def test_only_the_area_service_queries_place_names() -> None:
    """A second area search anywhere in ``src`` fails here, naming the file and line."""
    findings = [
        f"{path.relative_to(_SRC.parent)}:{line}"
        for path in sorted(_SRC.rglob("*.py"))
        if path not in _ALLOWED
        for line in _mentions_area_names(path.read_text(encoding="utf-8"))
    ]
    assert findings == [], "place names are read outside src/modules/discovery/areas.py"


def test_the_guard_catches_a_second_implementation() -> None:
    """Fed an adapter that queries names itself, it reports it; fed one that calls the service, it does not."""
    rogue = (
        "from src.database.models import AreaName\n"
        "def ussd_lookup(db, text):\n"
        "    return db.query(AreaName).filter(AreaName.name.ilike(text)).all()\n"
    )
    raw_sql = "SQL = 'SELECT area_id FROM area_name WHERE search_key % :k'\n"
    polite = (
        "from src.modules.discovery.areas import search_areas\n"
        "def ussd_lookup(db, text):\n"
        "    return search_areas(db, text)\n"
    )
    assert _mentions_area_names(rogue)
    assert _mentions_area_names(raw_sql)
    assert _mentions_area_names(polite) == []


def test_the_discovery_routes_call_the_area_service() -> None:
    """The web API is an adapter over the service, not a second search."""
    router = (_SRC / "modules" / "discovery" / "router.py").read_text(encoding="utf-8")
    assert "areas.search_areas(" in router
    assert "areas.recent_areas(" in router
