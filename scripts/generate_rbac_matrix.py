#!/usr/bin/env python3
"""Regenerate the RBAC matrix block of ``docs/architecture/rbac-matrix.md`` (Issue 18).

The derivation lives in :mod:`src.core.rbac_matrix`, shared with ``tests/test_rbac_matrix.py``, so
the document and its drift test can never disagree about what the table should say.

Usage:
    make rbac-matrix                          # rewrite the generated block
    scripts/generate_rbac_matrix.py --check   # exit 1 if the committed block is stale (no write)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.rbac_matrix import (  # noqa: E402 - after sys.path bootstrap
    MATRIX_DOC,
    build_block,
    committed_block,
    write_block,
)


def main(argv: list[str] | None = None) -> int:
    """Write the block, or (with ``--check``) report whether the committed one is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Do not write; exit 1 if stale."
    )
    args = parser.parse_args(argv)
    doc = REPO_ROOT / MATRIX_DOC
    derived = build_block()
    if args.check:
        if committed_block(doc) == derived:
            print(f"{MATRIX_DOC} is up to date.")
            return 0
        print(f"{MATRIX_DOC} is stale: run `make rbac-matrix` and review the diff.")
        return 1
    write_block(doc, derived)
    print(f"Wrote the RBAC matrix block in {MATRIX_DOC}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
