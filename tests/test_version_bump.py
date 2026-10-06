#!/usr/bin/env python3
"""Scenario tests for version-bump/check_version_bump.py.

The fixtures a version bump check needs are git histories, not files on disk,
so each test builds a real upstream repository, clones it, and commits a pull
request on top. Nothing is mocked: the script runs against real git output.

Run with: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "version-bump" / "check_version_bump.py"

MANIFEST = """{{
    "name": "Test module",
    "version": "{version}",
    "depends": ["base"],
}}
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class Result:
    def __init__(self, code: int, stdout: str, outputs: dict, summary: str):
        self.code = code
        self.stdout = stdout
        self.outputs = outputs
        self.summary = summary

    @property
    def checked(self) -> list[dict]:
        return json.loads(self.outputs.get("modules-checked", "[]"))

    @property
    def missing(self) -> list[str]:
        return json.loads(self.outputs.get("modules-missing-bump", "[]"))


class Scenario:
    """An upstream repository on `main` plus a clone with a feature branch."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.upstream = directory / "upstream"
        self.work = directory / "work"
        self.upstream.mkdir()
        git(self.upstream, "init", "--quiet", "--initial-branch", "main")
        git(self.upstream, "config", "user.email", "ci@example.com")
        git(self.upstream, "config", "user.name", "CI")

    # --- building ---------------------------------------------------------

    def write(self, repo: Path, path: str, content: str) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def module(self, repo: Path, name: str, version: str) -> None:
        self.write(repo, f"{name}/__manifest__.py", MANIFEST.format(version=version))
        self.write(repo, f"{name}/models.py", "# code\n")

    def commit(self, repo: Path, message: str) -> None:
        git(repo, "add", "--all")
        git(repo, "commit", "--quiet", "--allow-empty", "-m", message)

    def base(self, message: str = "base") -> None:
        """Commit the current upstream state as the base branch."""
        self.commit(self.upstream, message)

    def clone(self) -> Path:
        subprocess.run(
            ["git", "clone", "--quiet", str(self.upstream), str(self.work)],
            check=True,
            capture_output=True,
        )
        git(self.work, "config", "user.email", "ci@example.com")
        git(self.work, "config", "user.name", "CI")
        git(self.work, "checkout", "--quiet", "-b", "feature")
        return self.work

    def fetch(self) -> None:
        """Pick up commits made on upstream after the clone."""
        git(self.work, "fetch", "--quiet", "origin")

    # --- running ----------------------------------------------------------

    def run(self, modules_root: str = ".", base_ref: str = "origin/main") -> Result:
        output = self.directory / "github_output"
        summary = self.directory / "github_summary"
        output.write_text("", encoding="utf-8")
        summary.write_text("", encoding="utf-8")
        environment = dict(os.environ)
        environment["GITHUB_OUTPUT"] = str(output)
        environment["GITHUB_STEP_SUMMARY"] = str(summary)
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--modules-root",
                modules_root,
                "--base-ref",
                base_ref,
            ],
            cwd=self.work,
            capture_output=True,
            text=True,
            env=environment,
        )
        outputs = {}
        for line in output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        return Result(
            process.returncode,
            process.stdout + process.stderr,
            outputs,
            summary.read_text(encoding="utf-8"),
        )


class VersionBumpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario = Scenario(Path(self.tmp.name))

    # --- the core rule ----------------------------------------------------

    def test_bumped_module_passes(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/models.py", "# changed\n")
        self.scenario.module(work, "mod_a", "2.0.1")
        self.scenario.write(work, "mod_a/models.py", "# changed\n")
        self.scenario.commit(work, "change and bump")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(result.missing, [])
        self.assertEqual(
            result.checked,
            [{"module": "mod_a", "base": "2.0.0", "head": "2.0.1", "status": "ok"}],
        )

    def test_unbumped_module_fails_and_annotates_its_manifest(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/models.py", "# changed\n")
        self.scenario.commit(work, "change without a bump")

        result = self.scenario.run()

        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["mod_a"])
        self.assertIn("::error file=mod_a/__manifest__.py,line=3", result.stdout)
        self.assertIn("still 2.0.0", result.stdout)

    def test_translation_only_change_still_needs_a_bump(self):
        """No carve-outs: the rule is any file inside a module directory."""
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.write(self.scenario.upstream, "mod_a/i18n/de.po", "# po\n")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/i18n/de.po", "# translated\n")
        self.scenario.commit(work, "translation only")

        result = self.scenario.run()

        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["mod_a"])

    def test_decreased_version_fails_with_a_distinct_message(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.1.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_a", "2.0.9")
        self.scenario.commit(work, "downgrade")

        result = self.scenario.run()

        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["mod_a"])
        self.assertIn("went backwards", result.stdout)
        self.assertNotIn("still 2.0.9", result.stdout)
        self.assertEqual(result.checked[0]["status"], "decreased")

    def test_every_offender_is_reported_in_one_run(self):
        for name in ("mod_a", "mod_b", "mod_c"):
            self.scenario.module(self.scenario.upstream, name, "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/models.py", "# changed\n")
        self.scenario.write(work, "mod_b/models.py", "# changed\n")
        self.scenario.module(work, "mod_b", "2.0.1")
        self.scenario.write(work, "mod_c/models.py", "# changed\n")
        self.scenario.commit(work, "three modules, one bumped")

        result = self.scenario.run()

        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["mod_a", "mod_c"])
        self.assertEqual(len(result.checked), 3)

    # --- base tip, not merge base -----------------------------------------

    def test_compares_against_the_base_tip_not_the_merge_base(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_a", "2.0.1")
        self.scenario.commit(work, "bump to 2.0.1")

        # Someone else merges a bump to 2.1.0 while this pull request sits.
        self.scenario.module(self.scenario.upstream, "mod_a", "2.1.0")
        self.scenario.base("another bump lands on main")
        self.scenario.fetch()

        result = self.scenario.run()

        # Against the merge base this pull request did increase the version.
        # Against the tip it did not, and merging it would silently keep 2.0.1.
        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["mod_a"])
        self.assertIn("went backwards", result.stdout)

    # --- version comparison -----------------------------------------------

    def test_shorter_version_is_zero_padded(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.1.1")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_a", "2.1")
        self.scenario.commit(work, "2.1 is less than 2.1.1")

        result = self.scenario.run()

        self.assertEqual(result.code, 1, result.stdout)
        self.assertIn("went backwards", result.stdout)

    def test_five_part_odoo_versions_need_no_configuration(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "19.0.1.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_a", "19.0.1.0.1")
        self.scenario.commit(work, "bump the fifth segment")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)

    def test_non_numeric_segment_is_a_hard_error(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "1.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_a", "1.0.0-rc1")
        self.scenario.commit(work, "pre-release version")

        result = self.scenario.run()

        self.assertEqual(result.code, 2, result.stdout)
        self.assertIn("is not numeric", result.stdout)

    # --- manifests --------------------------------------------------------

    def test_unparseable_manifest_is_a_hard_error(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/__manifest__.py", "{'version': 2.0.0,,}\n")
        self.scenario.commit(work, "broken manifest")

        result = self.scenario.run()

        self.assertEqual(result.code, 2, result.stdout)
        self.assertIn("not a parseable manifest literal", result.stdout)
        self.assertEqual(result.missing, [])

    def test_manifest_without_a_version_key_is_a_hard_error(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/__manifest__.py", '{"name": "No version"}\n')
        self.scenario.commit(work, "manifest without a version")

        result = self.scenario.run()

        self.assertEqual(result.code, 2, result.stdout)
        self.assertIn("has no 'version' key", result.stdout)

    def test_manifest_is_never_imported(self):
        """literal_eval only: a manifest that would run code is an error."""
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        marker = Path(self.tmp.name) / "executed"
        self.scenario.write(
            work,
            "mod_a/__manifest__.py",
            "__import__('pathlib').Path(%r).write_text('x')\n" % str(marker),
        )
        self.scenario.commit(work, "manifest with a side effect")

        result = self.scenario.run()

        self.assertEqual(result.code, 2, result.stdout)
        self.assertFalse(marker.exists(), "the manifest was executed")

    # --- edge cases -------------------------------------------------------

    def test_new_module_passes(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.module(work, "mod_new", "1.0.0")
        self.scenario.commit(work, "add a module")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(result.checked[0]["status"], "new")
        self.assertIsNone(result.checked[0]["base"])

    def test_deleted_module_is_skipped(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.module(self.scenario.upstream, "mod_gone", "1.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        subprocess.run(
            ["git", "rm", "--quiet", "-r", "mod_gone"],
            cwd=work,
            check=True,
            capture_output=True,
        )
        self.scenario.commit(work, "remove a module")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(result.checked, [])
        self.assertIn("mod_gone", result.summary)
        self.assertIn("skipped", result.summary)

    def test_renamed_module_passes_and_is_noted(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        subprocess.run(
            ["git", "mv", "mod_a", "mod_renamed"],
            cwd=work,
            check=True,
            capture_output=True,
        )
        self.scenario.commit(work, "rename a module")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual([entry["status"] for entry in result.checked], ["new"])
        self.assertIn("mod_a", result.summary)
        self.assertIn("renamed or moved module", result.summary.lower())

    def test_repository_level_files_only_pass(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.write(self.scenario.upstream, "README.md", "# repo\n")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "README.md", "# repo, edited\n")
        self.scenario.write(work, ".github/workflows/ruff.yml", "name: Ruff\n")
        self.scenario.commit(work, "repository level only")

        result = self.scenario.run()

        self.assertEqual(result.code, 0, result.stdout)
        self.assertEqual(result.checked, [])
        self.assertEqual(result.missing, [])
        self.assertIn("No module files changed", result.summary)

    def test_missing_base_ref_is_a_hard_error(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.base()
        self.scenario.clone()

        result = self.scenario.run(base_ref="origin/never-fetched")

        self.assertEqual(result.code, 2, result.stdout)
        self.assertIn("fetch-depth: 0", result.stdout)

    def test_modules_root_limits_what_is_considered(self):
        self.scenario.module(self.scenario.upstream, "addons/mod_a", "2.0.0")
        self.scenario.module(self.scenario.upstream, "vendor/mod_b", "2.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "addons/mod_a/models.py", "# changed\n")
        self.scenario.write(work, "vendor/mod_b/models.py", "# changed\n")
        self.scenario.commit(work, "change both")

        result = self.scenario.run(modules_root="addons")

        self.assertEqual(result.code, 1, result.stdout)
        self.assertEqual(result.missing, ["addons/mod_a"])

    def test_nested_module_is_attributed_to_the_nearest_manifest(self):
        self.scenario.module(self.scenario.upstream, "mod_a", "2.0.0")
        self.scenario.module(self.scenario.upstream, "mod_a/nested", "1.0.0")
        self.scenario.base()
        work = self.scenario.clone()
        self.scenario.write(work, "mod_a/nested/models.py", "# changed\n")
        self.scenario.commit(work, "change the nested module")

        result = self.scenario.run()

        self.assertEqual(result.missing, ["mod_a/nested"])


if __name__ == "__main__":
    unittest.main()
