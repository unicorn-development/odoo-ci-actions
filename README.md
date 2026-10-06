# odoo-ci-actions

Reusable CI checks for Odoo module repositories. One central definition, so a
fix reaches every repository without being copied into each of them.

Today this repository provides one check, **ruff**. The name is deliberately
broad: further Odoo checks will live alongside it, one directory each, without
a rename that would break every caller.

## Usage

Add one file to a module repository:

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

That is the entire per-repository footprint. No ruff config to copy, no version
to maintain: `v1` is a moving major tag, so fixes propagate on their own.

Pin a full commit SHA instead of `v1` if you need immutability.

## What it does

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
4. If that fetch fails, the vendored copy in `ruff/configs/`, with a warning.

The check never falls back to bare ruff defaults, which would bury the diff
under findings Odoo does not enforce. Whichever source wins is written to the
job summary on every run.

The vendored copy is refreshed by a scheduled job that opens a pull request
whenever Odoo's `ruff.toml` changes.

### Findings do not block by default

A repository with existing violations stays mergeable: findings are annotated
and summarised, and the job still succeeds. Opt into blocking per repository
once it is clean:

```yaml
jobs:
  ruff:
    uses: unicorn-development/odoo-ci-actions/.github/workflows/ruff.yml@v1
    with:
      fail-on-findings: true
```

Two things always fail the job regardless of that setting:

- ruff exit code 2, meaning ruff could not run at all: an unparseable config,
  an unreadable file, a crash. Reporting that as "no findings" would be a green
  check carrying no information.
- Infrastructure failures: checkout, Python setup, ruff installation, or
  downloading a config given explicitly via the `config` input.

## Inputs

| Input | Default | Description |
|---|---|---|
| `config` | `''` | Explicit path or URL to a ruff config. Highest precedence. |
| `odoo-version` | `''` | odoo/odoo branch for the config. Empty means auto-detect. |
| `ruff-version` | `>=0.16.1` | pip version specifier, so it must include the operator. Odoo's config needs 0.16.1 or newer. |
| `python-version` | `3.12` | Python used to run ruff. |
| `paths` | `.` | Space separated paths to lint. |
| `fail-on-findings` | `false` | Whether findings fail the job. |

## Outputs

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

## Layout

```
ruff/
  action.yml                   composite action: config resolution, install, run, report
  configs/
    odoo-fallback.ruff.toml    vendored copy of odoo/odoo's ruff.toml
.github/workflows/
  ruff.yml                     workflow_call wrapper, the deployment layer
  test.yml                     self-test
  sync-odoo-config.yml         scheduled refresh of the vendored config
  release.yml                  moves the vN tag
tests/fixtures/                clean, dirty and broken-config fixtures
```

The split is deliberate. The action owns what the check does; the wrapper owns
the job shape. Changing how the check is deployed, from a caller stub to an
organisation ruleset, touches the wrapper only.

Note that this repository carries no ruff config at its root on purpose. One
would win resolution step 2 in every self-test and leave steps 3 and 4 untested.

## Development

`test.yml` runs on every pull request and asserts the clean fixture passes, the
dirty fixture annotates without failing and fails when blocking, an unparseable
config fails the job, and an unreachable branch falls back to the vendored
config. Without it, a bad release would disable linting in every consuming
repository at once, silently.

Releases are `vX.Y.Z`; publishing one moves the `vX` tag that callers track.
