# Release Process

Codified from the 0.6.x release cycle (2026-08-15) and finalized through
the 0.7.0 PPLX review. Every release follows these gates in order.

## 1. Pre-release health gate

Start with the doctor:

```bash
remagraph doctor --json --all-projects
```

Go/no-go rules (review-mandated):

| `overall` | Decision |
|---|---|
| `fail` | **Blocks the release.** Fix first. |
| `warn` | Requires an explicit sign-off from the release owner. |
| `ok` | Proceed. |

Note: doctor's exit code `2 = warn` is remagraph-specific — do not assume
cross-tool semantics in pipelines.

## 2. Full-codebase diagnostic (the 0.6.x methodology)

Per-commit review and whole-codebase scanning find **disjoint** bug sets:
cross-module composition errors (registry timing, cross-db id collisions,
hook × safety-valve interactions) are invisible to diff review. Before a
release:

1. **Whole-codebase diagnostic** — parallel reviewers sweep every module
   for correctness issues (not style), with confidence levels and
   concrete failure scenarios.
2. **Adversarial review of the fixes** — independent reviewers attempt to
   *refute* each fix and hunt for regressions the fixes introduced.
   Verify tests have teeth by reverting a sample of fixes and confirming
   the tests fail (commit first — reverts on an uncommitted tree destroy
   work).
3. **Acceptance scan** — a final sweep including any newly added modules.

## 3. Real-environment verification

Test environments lie by being clean. Registry state, shared-db strays,
case-insensitive filesystems, and bare environments (no `REMAGRAPH_*`
set) all differ from fixtures. Before tagging:

- Run the actual reported commands in the actual reported environment
  (0.6.2 shipped a migrate fix that passed every test and failed in the
  field because the real registry contained a poisoned entry; 0.6.3+
  releases were only tagged after reproducing on the reporter's exact
  invocation shapes).
- The Linux CI matrix is the last line of defense for platform
  differences masked by macOS's case-insensitive filesystem.

## 4. Ship

```bash
# bump pyproject + __init__ + README pins, update CHANGELOG, uv lock
git commit && git push
git tag vX.Y.Z-beta && git push origin vX.Y.Z-beta
```

The `publish.yml` workflow builds, publishes to PyPI (OIDC), and creates
the GitHub Release with notes extracted from the CHANGELOG section
(falling back to auto-generated notes with a visible warning if the
section is missing or empty).

## 5. Post-release

- `uv tool upgrade remagraph` locally; verify the fixed behavior once
  more on the released artifact.
- Record an `topic:infra-change` memory (cross-tower protocol) and notify
  dependent agents with concrete action items.
- After downstream verification, follow up with a `topic:infra-health`
  record closing the loop.
