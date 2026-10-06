#!/usr/bin/env bash
# Builds a deterministic version bump scenario in the current workspace, for
# .github/workflows/test.yml.
#
# The fixture a version bump check needs is a git history, so one is committed
# here rather than stored as files: a base state, then a head state that
# changes a module file. Overwriting refs/remotes/origin/<base> is local to the
# throwaway runner workspace and is what makes the scenario independent of
# whatever this pull request happens to change.
#
# Usage: version_bump_scenario.sh <base version> <head version|broken>
set -euo pipefail

base_version="$1"
head_version="$2"
module=tests/fixtures/version-bump/mod_a

manifest() {
  printf '{\n    "name": "Fixture",\n    "version": "%s",\n}\n' "$1" > "$module/__manifest__.py"
}

rm -rf tests/fixtures/version-bump
mkdir -p "$module"
manifest "$base_version"
echo "# base" > "$module/models.py"
git add --all tests/fixtures/version-bump
git commit --quiet -m "version bump fixture, base"
git update-ref "refs/remotes/origin/$GITHUB_BASE_REF" HEAD

if [ "$head_version" = "broken" ]; then
  printf '{"version": 2.0.0,,}\n' > "$module/__manifest__.py"
else
  manifest "$head_version"
fi
echo "# changed" > "$module/models.py"
git add --all tests/fixtures/version-bump
git commit --quiet -m "version bump fixture, head"
