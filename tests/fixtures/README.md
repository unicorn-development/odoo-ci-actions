# Test fixtures

Used by `.github/workflows/test.yml` only.

| Fixture | Purpose |
|---|---|
| `clean/` | Passes Odoo's ruff config. Asserts exit 0 / `has-findings=false`. |
| `dirty/` | Known violations. Asserts the blocking and non-blocking behaviour. |
| `broken-config/` | Unparseable config. Asserts ruff exit code 2 always fails the job. |
| `version-bump/` | Not stored here. Built by `../version_bump_scenario.sh` during the self-test, because the fixture a version bump check needs is a git history, not a file. |

Note that this repository deliberately has **no** ruff config at its root. One
would win config resolution step 2 in every self-test and leave steps 3 and 4
untested.

`version-sensitive/` is clean under `target-version = "py310"` and dirty under
`py312`, which is how the resolution tests tell which config actually won.
`GITHUB_STEP_SUMMARY` is a separate file per step, so a later step cannot read
what the action wrote there; the resolved config has to be asserted by effect.
