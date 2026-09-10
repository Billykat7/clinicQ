"""The scanning configuration says the same thing in every place it is written (Issue #180, M30).

M30 found the pipeline described three different ways at once: `vulnerability-scan.yml`'s header
claimed pip-audit ran in CI as a `dependency-scan` job, `ci.yml`'s header said the opposite, no such
job existed, and two release notes repeated the claim. Nobody was wrong on purpose — the facts were
written down four times and only three of them could be right.

These tests hold the small number of facts that *must* agree, and only those:

* the CVE ignore list is identical in the local script and the workflow — a CVE ignored in one and
  not the other is exactly how "but it passed locally" starts;
* the secret-scan baseline stays a short, reviewed list of test fixtures rather than a growing
  suppression file;
* no document claims a `dependency-scan` job in `ci.yml`, because there has never been one.

Offline and dependency-free: files are read, never run.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


CI_LOCAL = REPO_ROOT / "scripts" / "ci-local.sh"


SCAN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "vulnerability-scan.yml"


GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"


def _ignored_cves(text: str) -> set[str]:
    """Return every CVE/PYSEC id passed to ``--ignore-vuln`` anywhere in ``text``."""
    return set(re.findall(r"--ignore-vuln\s+([A-Za-z0-9\-]+)", text))


def test_the_cve_ignore_list_is_identical_locally_and_in_ci() -> None:
    """One list, two files. Drift here means a finding is invisible on exactly one side."""
    local = _ignored_cves(CI_LOCAL.read_text(encoding="utf-8"))
    workflow = _ignored_cves(SCAN_WORKFLOW.read_text(encoding="utf-8"))
    assert local == workflow, (
        f"pip-audit ignore lists differ — ci-local.sh: {sorted(local)}, "
        f"vulnerability-scan.yml: {sorted(workflow)}"
    )
    assert local, (
        "the ignore list is empty in both; if that is intended, delete this assertion"
    )


def test_every_ignored_cve_carries_a_written_reason() -> None:
    """An id with no rationale beside it is indistinguishable from a suppression of convenience."""
    text = SCAN_WORKFLOW.read_text(encoding="utf-8")
    for cve in _ignored_cves(text):
        # The reason must appear in the same file, near the id — a comment block, not a commit
        # message somebody would have to go looking for.
        assert re.search(rf"#.*{re.escape(cve)}", text), (
            f"{cve} is ignored with no comment explaining why"
        )


def test_the_gitleaks_allowlist_never_filters_by_path() -> None:
    """gitleaks applies an allowlist's ``paths`` *before* rules run, excluding the file entirely.

    Measured during Issue #180, and it is the trap this configuration exists to avoid: a
    ``paths = ['^tests/']`` allowlist would let a real AWS key or private key committed into a test
    file pass every rule. `matchCondition = "AND"` does not restore the file to the scan. So the
    config narrows by the *shape of the value* only, and historical fixture findings live in the
    enumerated baseline instead.
    """
    config = GITLEAKS_CONFIG.read_text(encoding="utf-8")
    allowlist = config[config.index("[[allowlists]]") :]
    assert "paths" not in allowlist, (
        "an allowlist `paths` entry silently removes those files from every rule — "
        "use .gitleaks-baseline.json for reviewed historical findings instead"
    )
