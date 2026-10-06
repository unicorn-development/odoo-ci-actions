# Test fixtures

Used by `.github/workflows/test.yml` only.

| Fixture | Purpose |
|---|---|
| `clean/` | Passes Odoo's ruff config. Asserts exit 0 / `has-findings=false`. |
| `dirty/` | Known violations. Asserts the blocking and non-blocking behaviour. |
| `broken-config/` | Unparseable config. Asserts ruff exit code 2 always fails the job. |

Note that this repository deliberately has **no** ruff config at its root. One
would win config resolution step 2 in every self-test and leave steps 3 and 4
untested.
