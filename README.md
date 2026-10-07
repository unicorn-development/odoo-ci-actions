# odoo-ci-actions

Reusable CI checks for Odoo module repositories. One central definition, so a
fix reaches every repository without being copied into each of them.

Two checks live here today, one directory each:

| Check | Catches | Blocks |
|---|---|---|
| [ruff](#ruff) | Lint findings, using Odoo's own configuration | Only where a ruleset requires it |
| [version bump](#version-bump) | A module changed without raising its manifest version | Only where a ruleset requires it |

## Usage

Add one file per check to a module repository:

```yaml
# .github/workflows/ruff.yml
name: Ruff
on:
  pull_request:
    branches:
      - main
      # Odoo version branches, e.g. 19.0, 18.0, 17.0
      - "[0-9]*.[0-9]*"
permissions:
  contents: read
jobs:
  ruff:
    uses: unicorn-development/odoo-ci-actions/.github/workflows/ruff.yml@v1
```

```yaml
# .github/workflows/version-bump.yml
name: Version bump
on:
  pull_request:
    branches:
      - main
      - "[0-9]*.[0-9]*"
permissions:
  contents: read
jobs:
  version-bump:
    uses: unicorn-development/odoo-ci-actions/.github/workflows/version-bump.yml@v1
```

That is the entire per-repository footprint. No ruff config to copy, no version
to maintain: `v1` is a moving major tag, so fixes propagate on their own.

Pin a full commit SHA instead of `v1` if you need immutability, and see
[how the wrapper finds its own action](#how-the-wrapper-finds-its-own-action):
pinning the wrapper does not by itself pin the action.

Note that neither workflow uses a `paths:` filter, on purpose. A required
status check that never runs leaves a pull request waiting for a conclusion
that never arrives; "nothing relevant changed" is handled by the check passing,
not by the workflow being skipped.

## Ruff

```
ruff check --no-fix --output-format=github --config <resolved config> <paths>
```

Findings appear as annotations on the pull request diff. `--no-fix` is not
configurable: the check never modifies code and never pushes, and the workflow
requests only `contents: read` so the token cannot do so either.

### Which rules are used

Odoo's own, resolved in this order:

1. The `config` input, when set. A path or a URL.
2. The repository's own config: `.ruff.toml`, `ruff.toml`, or a `pyproject.toml`
   containing a `[tool.ruff]` table. Ruff discovers it as usual.
3. `https://raw.githubusercontent.com/odoo/odoo/<branch>/ruff.toml`, where
   `<branch>` is the `odoo-version` input; otherwise the pull request base
   branch when it looks like an Odoo version (`19.0`, `18.0`); otherwise
   `master`. A 17.0 module is therefore linted with 17.0's rules.
4. If that fetch fails, the vendored copy in `ruff-checks-stuff/configs/`, with a
   warning.

The check never falls back to bare ruff defaults, which would bury the diff
under findings Odoo does not enforce. Whichever source wins is written to the
job summary on every run.

The vendored copy is refreshed by a scheduled job that opens a pull request
whenever Odoo's `ruff.toml` changes.

### What fails the job

Findings fail it. So does ruff exit code 2, meaning ruff could not run at all:
an unparseable config, an unreadable file, a crash. Reporting that as "no
findings" would be a green check carrying no information. So do infrastructure
failures: checkout, Python setup, ruff installation, or downloading a config
given explicitly via the `config` input.

A failing check is not the same as a blocked merge. GitHub blocks a merge only
on a *required* status check, so a repository with existing violations stays
mergeable while `ruff / Ruff` is absent from the ruleset: the pull request goes
red, the annotations are on the diff, and the merge button still works. Require
the check once the repository is clean.

There is deliberately no soft mode. A GitHub Actions job can only conclude
success, failure, cancelled or skipped -- there is no amber "failed but
mergeable" -- so a non-failing mode meant a green check carrying findings
nobody had to look at, which is the failure mode this repository exists to
avoid.

### Inputs

| Input | Default | Description |
|---|---|---|
| `config` | `''` | Explicit path or URL to a ruff config. Highest precedence. |
| `odoo-version` | `''` | odoo/odoo branch for the config. Empty means auto-detect. |
| `ruff-version` | `>=0.16.1` | pip version specifier, so it must include the operator. Odoo's config needs 0.16.1 or newer. |
| `python-version` | `3.12` | Python used to run ruff. |
| `paths` | `.` | Space separated paths to lint. |
| `actions-ref` | `v1` | Ref this repository's action is taken from. See [pinning](#how-the-wrapper-finds-its-own-action). |

### Outputs

| Output | Description |
|---|---|
| `exit-code` | Raw ruff exit code: 0 clean, 1 findings, 2 error. |
| `has-findings` | `true` or `false`. |

### Why `python-version` is fixed

Ruff is a self-contained binary, and the interpreter that launches it has no
effect on lint results. The syntax level is set by `target-version` in the
resolved config, and Odoo's per-branch `ruff.toml` already carries the right
value. Because the config is fetched from the matching version branch, version
matching already happens where it actually matters. Pinning the runner Python
costs nothing in accuracy and keeps output from changing when a branch's
supported interpreter moves. It remains an input for the rare override.

## Version bump

Odoo only runs migration scripts and re-registers assets when a module's
manifest version increases. A pull request that changes module code but leaves
`version` in `__manifest__.py` untouched therefore ships code that is never
properly upgraded on existing databases, and the failure surfaces later, in
production, as stale assets or an inconsistent schema.

### The rule

**Any change to any file inside a module directory requires that module's
version to increase.** No ignore list, no carve-out for translations, README or
lint-only churn.

Deliberately blunt: an unnecessary bump costs nothing, a missed bump costs a
production incident, and every clever heuristic here fails *open* -- the wrong
direction for a check whose whole purpose is catching an omission.

A module is the nearest ancestor directory of a changed file that contains an
`__manifest__.py`. Files belonging to no module -- `.ruff.toml`,
`.editorconfig`, `.github/**`, the repository README -- need no bump. All
touched modules are evaluated before the check exits, so a multi-module pull
request reports every offender in one run.

Manifests are read with `ast.literal_eval` on the file source. A manifest is a
bare dict literal, so no Odoo import is needed and repository code is never
executed.

### Compared against the base tip, not the merge base

Versions are compared against the **current tip** of the base branch. If
another pull request merged a bump to `2.1.0` while this one sat at `2.0.1`, a
merge-base comparison would pass and the merge would silently keep `2.0.1` --
precisely the bug this check exists to prevent.

The cost is that a stale pull request may need a second bump after someone else
merges. That is the correct trade for a blocking check.

### Version comparison

Versions compare as tuples of integers, zero padded to equal length, so `2.1`
is less than `2.1.1`. That covers our 3-part scheme (`2.0.0`) and upstream
Odoo's 5-part `19.0.1.0.0` without any scheme input.

A non-numeric segment, such as `1.0.0-rc1`, is a hard error with an explicit
message rather than a guess about what was meant.

### What fails the job

A missing bump fails it, and so does a check that could not run at all: an
unparseable manifest, a manifest without a `version` key, a base branch that
was never fetched, or a run outside a pull request. None of those are ever
reported as a pass.

There is no input to turn that off. Staging a rollout is the ruleset's job --
see [making it actually block](#making-it-actually-block) -- and an input that
made the job succeed on a missing bump would instead *satisfy* the required
check, merging exactly what the check exists to catch.

There are no ignore globs, no scheme selection and no skip label. If a pull
request is genuinely a false positive, bumping the version is cheaper than
configuring an exception, and administrators can still override a required
check.

### Inputs

| Input | Default | Description |
|---|---|---|
| `modules-root` | `.` | Directory the modules live in. Changed files outside it are ignored. |
| `actions-ref` | `v1` | Ref this repository's action is taken from. See [pinning](#how-the-wrapper-finds-its-own-action). |

### Outputs

| Output | Description |
|---|---|
| `modules-checked` | JSON array of `{module, base, head, status}`, one per touched module. `status` is `ok`, `new`, `missing-bump` or `decreased`. |
| `modules-missing-bump` | JSON array of the modules that changed without a version increase. |

### Edge cases

| Case | Behaviour |
|---|---|
| New module, no manifest on the base branch | Pass, there is nothing to bump against |
| Module deleted in the pull request | Skipped, noted in the job summary |
| Module renamed or moved | One new and one deleted module, so it passes; noted in the summary |
| Version decreased | Fail, with a message distinct from "not bumped" |
| Repository level files only | Pass, no modules considered |
| Unparseable manifest, or no `version` key | Hard error, distinct from a missing bump |
| Base branch not fetched | Hard error |

### Making it actually block

A failing check is red but still mergeable unless it is a **required status
check**. Mark `Version bump` required via an organisation ruleset, or branch
protection, on `main` and `[0-9]*.[0-9]*`. Two things have to stay right:

- The check name must be stable. Renaming the job silently drops the
  requirement and leaves branches unprotected, so `name: Version bump` is
  pinned in the wrapper.
- The workflow must not use a `paths:` filter, or a pull request that changes
  nothing relevant would wait forever for a check that never reports.

## How the wrapper finds its own action

A reusable workflow cannot reach files in its own repository with `uses: ./`,
because relative paths resolve against the caller's workspace. It cannot
discover its own ref either: `github.job_workflow_sha` and
`github.job_workflow_ref` are both empty in a called workflow's context
([actions/runner#2417](https://github.com/actions/runner/issues/2417)), and a
checkout without an explicit ref silently takes the default branch -- so the
action would come from `main` whatever tag the caller pinned, with no error to
show for it.

Each wrapper therefore checks this repository out at the major tag, `v1`, which
`release.yml` moves to the release commit. Wrapper and action travel together
for every caller on `@v1`, which is all of them.

The consequence is that pinning a wrapper to a full commit SHA does not pin the
action with it: the action still comes from `v1`. Override `actions-ref` to pin
both, or to test an unreleased branch of this repository from a consumer
repository.

## Layout

```
ruff-checks-stuff/
  action.yml                   composite action: config resolution, install, run, report
  configs/
    odoo-fallback.ruff.toml    vendored copy of odoo/odoo's ruff.toml
version-bump/
  action.yml                   composite action: job shape glue around the check
  check_version_bump.py        the check: diff, grouping, manifest parsing, comparison
.github/workflows/
  ruff.yml                     workflow_call wrapper, the deployment layer
  version-bump.yml             workflow_call wrapper, the deployment layer
  test.yml                     self-test
  sync-odoo-config.yml         scheduled refresh of the vendored config
  release.yml                  moves the vN tag
tests/
  fixtures/                    clean, dirty and broken-config fixtures for ruff
  test_version_bump.py         scenario tests for the version bump check
  version_bump_scenario.sh     builds the fixture history for the action self-test
```

The split is deliberate. Each action owns what its check does; the wrapper owns
the job shape. Changing how a check is deployed, from a caller stub to an
organisation ruleset, touches the wrapper only.

The version bump logic is Python rather than shell because manifest parsing and
version comparison deserve unit tests.

Note that this repository carries no ruff config at its root on purpose. One
would win resolution step 2 in every self-test and leave steps 3 and 4 untested.

## Development

`test.yml` runs on every pull request. For ruff it asserts the clean fixture
passes, the dirty fixture annotates without failing and fails when blocking, an
unparseable config fails the job, and an unreachable branch falls back to the
vendored config. For the version bump check it runs the scenario tests, and
then drives the action itself through a bumped, an unbumped and an unparseable
fixture to cover the shell glue around the exit code. Without all of this, a
bad release would disable a check in every consuming repository at once,
silently.

Releases are `vX.Y.Z`; publishing one moves the `vX` tag that callers track.
Adding a check is a minor bump, so consumers on `@v1` get it automatically.
