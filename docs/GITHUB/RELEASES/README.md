# Releases

One file per tag: `RELEASE_v<major>_<minor>_<patch>.md`. A release note is a record of what shipped on
that date; it is never rewritten later to match how the product looks now.

## Versioning rule

**One minor version per milestone. `v1.0.0` only when the last issue in the last milestone is closed.**

```text
v0.1.0  M1   ...  v0.14.0  M14        →   v1.0.0
└── every 0.x is a pre-release ──┘         first official release:
    the product is not finished yet        issues 1–109 all closed
```

- A milestone's tag is cut **when its last issue closes**, not when its sprint ends.
- Patch releases (`v0.6.1`) fix a milestone that is already tagged: a queue-engine bug found during M7
  is `v0.6.1`, not part of `v0.7.0`.
- `v1.0.0` means one thing only: **every issue in every milestone is done.** Going live at the pilot
  clinic happens inside M14, under `v0.14.0`.
- After `v1.0.0`, normal semantic versioning: backlog features `v1.1.0`+, fixes `v1.0.1`+, breaking
  changes `v2.0.0`.

Full table, including which issues belong to which tag:
[`../README.md#release-tags`](../README.md#release-tags).

## Template

```markdown
# Release v0.6.0: Queue Engine Core

**Date:** YYYY-MM-DD · **Milestone:** M6 · **Issues closed:** 39–47

## What shipped

- …

## Migrations

- `0012_tickets`: creates `tickets` and the daily sequence constraint. **Not reversible** once tickets
  exist.

## Upgrade notes

- …

## Known issues

- …
```

The `v1.0.0` note is the exception to the template. It summarises the **whole project**: every
milestone, what the system does end to end, and what the pilot proved, because it is the first
release anyone outside the team is meant to read.
