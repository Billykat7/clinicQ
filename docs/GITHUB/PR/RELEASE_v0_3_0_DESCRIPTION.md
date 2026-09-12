# Release v0.3.0: the M3 release note, a readable front page, and milestone progress that maintains itself

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) (closed) ·
**Tag to cut after merge:** `v0.3.0`

Every M3 issue (#15–#22) is closed and every PR (#129–#136) merged, so the tag's precondition holds
and the release note is written. Three other things ride with it: the front page's picture, a setup
guide that does not assume macOS, and the answer to "how far along is this milestone?" being a
generated fact rather than a sentence somebody forgot to update.

## Summary

- **`docs/GITHUB/RELEASES/RELEASE_v0_3_0.md`** — from the template in `RELEASES/README.md`: what
  shipped per issue, migrations `0002`–`0006`, upgrade notes, and an honest known-issues list.
- **M3 is marked done** in `M3_identity_auth_rbac.md`, with **two exit criteria left unticked** and
  the reason written beside each (below).
- **The "What ClinicQ does" diagram is now mermaid**, not ASCII art.
- **`## Quickstart`** replaces `## Getting started`: prerequisites for **Windows, macOS and Linux**,
  then run it locally, then branch → commit → `make check` → pull request.
- **`scripts/update_milestone_progress.py`** (+ `make milestone-progress`,
  `milestone-progress-check`): a green progress bar and percentage in every milestone document and in
  the README's delivery table, read from GitHub's own issue states.

## The release note

Written to the template, and measured rather than remembered. Two numbers in the first draft were
wrong and were corrected against the source before committing: `.env.example` has **118** settings,
not 113 (`Settings.model_fields` says so), and "the image runs one worker" is true because
`infra/docker/Dockerfile:152` runs uvicorn with no `--workers` flag.

Migrations `0002`–`0006` each get an entry saying why the previous release still runs on the new
schema, including why nothing in `0004` is backfilled (a backfill is exactly the `UPDATE` the
append-only trigger exists to refuse) and why `family_id` stays nullable for now.

The upgrade notes carry what actually breaks someone: the four portal roles are gone with **no
migration**, so anyone holding one keeps a string that names a role with no grants; `GET
/audit/events` now needs `audit:read` rather than `logs:read`; writes need the CSRF header.

Known issues are the honest list — the process-local OTP store forcing one worker, F's consent
wording committed but **not yet reviewed**, the three audit proofs that cannot exist until Issues 46,
27 and 43, `actor_role` not filled centrally, no `site` table until Issue 23, and every open item
from v0.2.0 still standing.

## The diagram

Explicit `fill`/`stroke`/`color` on every class, so it reads the same in GitHub's light and dark
themes instead of inheriting a theme that inverts half of it. Verified by rendering the exact block
from `README.md` with **mermaid 11 at `securityLevel: "strict"`** — what GitHub runs — on a white and
a `#0d1117` page: identical, and the `<b>`/`<small>` labels survive the sanitiser.

## Quickstart

It is its own page — **[`docs/QUICKSTART.md`](../../QUICKSTART.md)** — and the **first link in the
README's header nav**, before *Implementation plan*, because "how do I run this?" is the first
question a new reader has. The README keeps a five-command summary that points at it, and
`CONTRIBUTING.md` opens by sending a first-time setup there.

`## Getting started` assumed you already had Python 3.14, Docker and `make`. The page says how to
get them, per platform, in a `<details>` block each:

- **Windows:** WSL2, and the reason it is not optional — `make` and everything in `scripts/` assume a
  Unix shell, so Git Bash alone will not do. Plus the trap that costs an afternoon: clone into the
  Linux filesystem, not `/mnt/c/`.
- **macOS:** Homebrew, with the Apple Silicon note (amd64-only PostGIS images run under emulation).
- **Linux:** Debian/Ubuntu, Fedora and Arch, and the `docker` group.

Then **§3, branch → commit → PR**, as commands you can paste, and a table of what CI checks with the
script that checks it — because those rules are enforced, not stylistic.

## Milestone progress

The rule asked for: **every PR updates the docs and its milestone's status, and the last issue of a
milestone marks that milestone done, each showing a green progress bar with a percentage.**

`make milestone-progress` reads issue states from GitHub — `gh issue list`, which counts issues and
never pull requests — and writes into three places: the milestone document's header table, the
README's delivery table, and the **Milestone summary** table in `docs/GITHUB/README.md`, whose
Status column read `📋 planned` for all fourteen milestones (three of which are finished):

```text
| **Progress** | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues) |
| **Progress** | ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** (0/9 issues) |
```

**Why emoji and not a badge image:** it renders on GitHub, in an editor and in a terminal, with no
image host, no network and nothing to rot. Ten cells, so a percentage rounds to a cell cleanly.

`milestone-progress-check` fails when a document disagrees with GitHub and names it, for use before
pushing. `ARGS='--close-completed'` closes a GitHub milestone whose last issue is closed — **M1, M2
and M3 are now closed there**; they had been left open.

The rule itself lives in `.cursor/rules/milestone-progress.mdc` and is summarised in
`CONTRIBUTING.md`, which is what the README links to (see *Note on `.cursor/`* below).

## Sprints

The plan put sprints 1–7 in semester 1 and 8–14 in semester 2. **All fourteen run in semester 2**, so
the split is gone: `WORKLOAD_SPLIT.md` §3 is one table, and the six milestone documents that said
"semester 1" now say semester 2.

It also gained a *Where we are* table — sprint, weeks, the milestones it carries, and its status —
with **sprints 1–4 marked done** (they carry M1–M3, issues 1–22, tags `v0.1.0`–`v0.3.0`), sprint 5
marked next, and a `✅` on those four rows in the lanes table.

**Why this one is marked by hand and the milestone bars are not:** a lane names issues it works
*against* as well as issues it delivers — sprint 3 works against the notification contract stub
`[63]` and a discovery fixture `[32]`, neither of which is sprint 3's to close, and `[→95]` is an
explicit forward reference. A script counting brackets would call sprint 3 unfinished. The document
says so where the table is, so nobody later mistakes it for generated output.

The root README's Status block was still "planning complete. Implementation begins at M1" with M1
and M2 unticked; it now reads sprint 4 of 14, 22 of 109 issues, `v0.3.0`.

## Changes

- **New:** `docs/GITHUB/RELEASES/RELEASE_v0_3_0.md`, `scripts/update_milestone_progress.py`.
- **New:** `docs/QUICKSTART.md` — prerequisites per OS, run locally, branch/commit/PR, and the
  troubleshooting and local-stack notes that used to sit in the README.
- **`README.md`:** mermaid diagram; **Quickstart first in the header nav** and in the documentation
  table; a five-command Quickstart section pointing at the page; a **Progress** column on the
  delivery table.
- **`CONTRIBUTING.md`:** the milestone-progress step in "Branches, commits and pull requests".
- **`Makefile`:** `milestone-progress`, `milestone-progress-check`.
- **`docs/GITHUB/README.md`:** the *Milestone summary* table's **Status** column, and a generated
  roll-up line under it (`🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜ **20%** (22/109 issues) closed · **3 of 14 milestones
  done**`).
- **All 14 milestone documents:** a **Progress** row. M3 additionally: status, and its exit criteria.
  Six of them: `semester 1` → `semester 2`.
- **`docs/TEAM/WORKLOAD_SPLIT.md`:** one sprint table instead of two, the *Where we are* status
  table, and `✅` on sprints 1–4.

## Testing

- [x] `ruff check .`, `ruff format --check .` (339 files), `mypy scripts/update_milestone_progress.py`
      clean.
- [x] `pytest -q -n auto`: **1190 passed**, 20 skipped, 9 xfailed. No test changed; this PR is docs
      and tooling.
- [x] `scripts/check_pr_conventions.check()` against this branch: **19 files, problems: NONE**.
- [x] **The script is idempotent and its check is honest:** run twice, the second run reports "up to
      date"; `--check` then exits 0. Before the fix below it exited 1 and named the file.
- [x] **Labels synced:** `make gh-sync-labels` → `created=0 updated=0 unchanged=42 stale=0 pruned=0`.
      They were already in sync; nothing changed.
- [x] **GitHub milestones:** 1, 2 and 3 now report `closed`.

```text
$ make milestone-progress
  M1  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
  M2  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (6/6 issues)
  M3  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
  M4  ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** (0/8 issues)
  …
  overall (README ⭐ row)  🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜ **20%** (22/109 issues)
```

**Found while writing it:** the first version of the README rewriter matched *any* markdown table
after the delivery table's header and mangled two others. It now finds that one table by its header
line, stops at the first line that is not part of it, and rebuilds each row from its first five
cells — which is also what makes a second run a no-op instead of a second column.

## Note on `.cursor/`

`.cursor/` is ignored by a **global** gitignore and has never been tracked in this repository, so
`milestone-progress.mdc` is **not in this diff**. The rule is therefore written down twice on
purpose: in the editor rule locally, and in `CONTRIBUTING.md` + the Quickstart table, which is what
the README links to so the link is not broken for anyone who clones. If the team would rather ship
the editor rules with the repository, that is a one-line `.gitignore` change and a `git add -f` —
worth deciding deliberately rather than by accident.

## Risk and rollback

Documentation, a Makefile target and one script that runs only when invoked. No application code, no
migration, no CI change. Rollback is a revert. The one outward change is that GitHub milestones 1–3
are now closed; reopening them is a click.

After merge, cut the tag on `main`: `git tag v0.3.0 && git push origin v0.3.0`, which builds and
publishes the image and starts the staging deploy (`docs/CICD/RELEASE.md`).

Closes nothing: issues 15–22 were closed by their own pull requests (#129–#136).
