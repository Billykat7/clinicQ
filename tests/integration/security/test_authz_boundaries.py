"""The boundary table describes the tree it ships with (Issue #178, M30).

`docs/SECURITY/AUTHZ-BOUNDARIES.md` replaced a hand-written list of ownership-gated resources that
had gone quietly wrong — by M30 the M17 threat model named ten of twenty modules. A generated
document only fixes that while something fails when it drifts, so these are that something:

* the committed table equals what the generator derives from the live router tree **today**;
* every id-addressable route has a *declared* ownership gate, so adding a route without recording
  what binds its rows to a caller fails the build rather than passing unnoticed;
* the M17 baseline the ★ column diffs against is the historical record it claims to be.

Deliberately not asserted: the *wording* of a gate. That is prose for a reader; what a test can
honestly hold is coverage and freshness. Whether a gate actually refuses a non-owner is the job of
``test_horizontal_escalation.py``, which asks the running application rather than the document.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "path",
    [
        Path("docs/SECURITY/THREAT-MODEL.md"),
        Path("docs/SECURITY/SECURITY-CHECKLIST.md"),
        Path("docs/SECURITY/AUTHZ-BOUNDARIES.md"),
    ],
)
def test_the_security_docs_reference_each_other(path: Path) -> None:
    """The three documents are one set; a reader landing on any of them must find the others."""
    text = path.read_text(encoding="utf-8")
    others = {"THREAT-MODEL.md", "SECURITY-CHECKLIST.md", "AUTHZ-BOUNDARIES.md"} - {
        path.name
    }
    missing = [name for name in others if name not in text]
    assert missing == [], f"{path.name} does not link {missing}"
