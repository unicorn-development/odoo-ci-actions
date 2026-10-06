# UP017 (use datetime.UTC) only fires for target-version py311 and newer, so
# this file is clean under Odoo 19.0's config (py310) and dirty under master's
# (py312). That makes which config was resolved observable from the outside,
# without the action having to expose it as an output. Bare ruff defaults do
# not enable UP at all, so a clean result here also proves they were not used.
from datetime import timezone

EPOCH_TZ = timezone.utc
