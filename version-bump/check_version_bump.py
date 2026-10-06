#!/usr/bin/env python3
"""Fail a pull request that changes an Odoo module without raising its version.

Odoo only runs migration scripts and re-registers assets when a module's
manifest version increases, so an un-bumped change ships code that is never
properly upgraded on existing databases. The failure surfaces later, in
production, as stale assets or an inconsistent schema.

The rule is deliberately blunt: any change to any file inside a module
directory requires that module's version to increase. No ignore list, no
carve-out for translations, README or lint-only churn. An unnecessary bump
costs nothing, a missed bump costs a production incident, and every clever
heuristic here would fail open -- the wrong direction for a check whose whole
purpose is catching an omission.

Exit codes follow the ruff action: 0 nothing to report, 1 modules are missing a
bump, 2 the check could not run and its result carries no information.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = "__manifest__.py"
NUMERIC = re.compile(r"[0-9]+\Z")


class CheckError(Exception):
    """A condition the check cannot work around. Always exit 2, never a pass."""


# --- git ---------------------------------------------------------------------


def git(*args: str) -> str:
    """Run git and return stdout, turning any failure into a hard error."""
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CheckError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def git_succeeds(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, text=True).returncode == 0
    )


@functools.lru_cache(maxsize=None)
def blob_exists(ref: str, path: str) -> bool:
    return git_succeeds("cat-file", "-e", f"{ref}:{path}")


def blob(ref: str, path: str) -> str | None:
    if not blob_exists(ref, path):
        return None
    return git("show", f"{ref}:{path}")


def require_ref(ref: str) -> None:
    if not git_succeeds("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
        raise CheckError(
            f"Base ref '{ref}' is not available. The workflow must check out with "
            "fetch-depth: 0 so the base branch is fetched."
        )


def changed_files(base_ref: str) -> list[str]:
    """Files this pull request changed, relative to the merge base.

    Rename detection is off on purpose: --name-only reports only the new path
    of a rename, which would hide the module a move emptied out.
    """
    out = git("diff", "--name-only", "--no-renames", "-z", f"{base_ref}...HEAD")
    return [path for path in out.split("\0") if path]


# --- manifests ---------------------------------------------------------------


def manifest_path(module: str) -> str:
    return MANIFEST if module == "." else f"{module}/{MANIFEST}"


def read_manifest(source: str, origin: str) -> tuple[str, int]:
    """Return a manifest's version and the line its key sits on.

    literal_eval, so an Odoo import is never needed and repository code is
    never executed.
    """
    try:
        tree = ast.parse(source, mode="eval")
        manifest = ast.literal_eval(tree)
    except (SyntaxError, ValueError) as exc:
        raise CheckError(
            f"{origin} is not a parseable manifest literal: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise CheckError(
            f"{origin} is not a dict literal, it is a {type(manifest).__name__}"
        )
    if "version" not in manifest:
        raise CheckError(f"{origin} has no 'version' key")
    version = manifest["version"]
    if not isinstance(version, str):
        raise CheckError(f"{origin} has a non-string version {version!r}")
    return version, version_key_line(tree)


def version_key_line(tree: ast.Expression) -> int:
    if isinstance(tree.body, ast.Dict):
        for key in tree.body.keys:
            if isinstance(key, ast.Constant) and key.value == "version":
                return key.lineno
    return 1


def version_tuple(raw: str, origin: str) -> tuple[int, ...]:
    """Versions are dot separated integers, nothing else.

    Covers our 3-part scheme (2.0.0) and upstream Odoo's 5-part 19.0.1.0.0
    without any scheme input. A segment that is not an integer is a hard error
    rather than a guess about what the author meant.
    """
    segments = raw.split(".")
    for segment in segments:
        if not NUMERIC.match(segment):
            raise CheckError(
                f"{origin} has version '{raw}': segment '{segment}' is not numeric. "
                "Versions must be dot separated integers, e.g. 2.0.1 or 19.0.1.0.0."
            )
    return tuple(int(segment) for segment in segments)


def compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare zero padded to equal length, so 2.1 is less than 2.1.1."""
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return (left > right) - (left < right)


# --- grouping ----------------------------------------------------------------


def ancestors(path: str, root: str):
    """Directories of `path` from nearest to `root`, skipping those outside it."""
    directory = posixpath.dirname(path) or "."
    while True:
        if root == "." or directory == root or directory.startswith(f"{root}/"):
            yield directory
        if directory in (root, "."):
            return
        directory = posixpath.dirname(directory) or "."


def locate(path: str, root: str, base_ref: str) -> tuple[str | None, str | None]:
    """The nearest ancestor directory of `path` that carries a manifest."""
    for directory in ancestors(path, root):
        manifest = manifest_path(directory)
        if Path(manifest).is_file():
            return directory, "head"
        if blob_exists(base_ref, manifest):
            return directory, "base"
    return None, None


def group(changed: list[str], root: str, base_ref: str) -> tuple[list[str], list[str]]:
    touched: dict[str, None] = {}
    deleted: set[str] = set()
    for path in changed:
        module, where = locate(path, root, base_ref)
        if module is None:
            continue  # Repo level file: .ruff.toml, .github/**, the README.
        if where == "head":
            touched[module] = None
        else:
            deleted.add(module)  # The module is gone in this pull request.
    return list(touched), sorted(deleted)


# --- reporting ---------------------------------------------------------------


def annotate(path: str, line: int, message: str) -> None:
    print(f"::error file={path},line={line},title=Version bump::{message}")


def append(variable: str, text: str) -> None:
    destination = os.environ.get(variable)
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")


def set_output(name: str, value: str) -> None:
    append("GITHUB_OUTPUT", f"{name}={value}")


def summary(text: str) -> None:
    append("GITHUB_STEP_SUMMARY", text)


RESULTS = {
    "ok": "bumped",
    "new": "new module, nothing to bump against",
    "missing-bump": "**not bumped**",
    "decreased": "**version decreased**",
}


def report(checked: list[dict], offenders: list[str], deleted: list[str]) -> None:
    summary("### Version bump")
    summary("")
    if not checked and not deleted:
        summary("No module files changed, nothing to check.")
        print("No module files changed, nothing to check.")
        return

    if checked:
        summary("| Module | Base | Head | Result |")
        summary("|---|---|---|---|")
        for entry in checked:
            base = entry["base"] or "--"
            summary(
                f"| `{entry['module']}` | {base} | {entry['head']} | "
                f"{RESULTS[entry['status']]} |"
            )
        summary("")

    if deleted:
        listed = ", ".join(f"`{module}`" for module in deleted)
        summary(f"Deleted in this pull request, skipped: {listed}.")
        summary("")
        summary(
            "A renamed or moved module shows up as one deleted and one new module. "
            "Neither needs a bump."
        )
        summary("")

    if offenders:
        summary(
            f"{len(offenders)} module(s) changed without a version increase. "
            "See the annotations on their manifests."
        )
    print(f"Checked {len(checked)} module(s), {len(offenders)} missing a bump.")


# --- the check ---------------------------------------------------------------


def evaluate(module: str, base_ref: str) -> dict:
    manifest = manifest_path(module)
    try:
        source = Path(manifest).read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckError(f"{manifest} could not be read: {exc}") from exc

    head_version, line = read_manifest(source, manifest)
    entry = {"module": module, "base": None, "head": head_version, "status": "new"}

    base_source = blob(base_ref, manifest)
    if base_source is None:
        return entry  # New module: there is nothing to bump against.

    base_origin = f"{base_ref}:{manifest}"
    base_version, _ = read_manifest(base_source, base_origin)
    entry["base"] = base_version

    order = compare(
        version_tuple(head_version, manifest), version_tuple(base_version, base_origin)
    )
    if order > 0:
        entry["status"] = "ok"
    elif order == 0:
        entry["status"] = "missing-bump"
        annotate(
            manifest,
            line,
            f"{module} changed but its version is still {head_version}. Increase "
            f"'version' in {manifest}.",
        )
    else:
        entry["status"] = "decreased"
        annotate(
            manifest,
            line,
            f"{module}'s version went backwards, from {base_version} on "
            f"{base_ref} to {head_version}. It must increase.",
        )
    return entry


def check(base_ref: str, root: str) -> int:
    require_ref(base_ref)
    touched, deleted = group(changed_files(base_ref), root, base_ref)

    # Every touched module is evaluated before returning, so a multi-module
    # pull request reports every offender in one run.
    checked = [evaluate(module, base_ref) for module in touched]
    offenders = [
        entry["module"]
        for entry in checked
        if entry["status"] in ("missing-bump", "decreased")
    ]

    report(checked, offenders, deleted)
    set_output("modules-checked", json.dumps(checked))
    set_output("modules-missing-bump", json.dumps(offenders))
    return 1 if offenders else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Odoo manifest version bump check.")
    parser.add_argument(
        "--modules-root",
        default=".",
        help="Directory the modules live in. Files outside it are ignored.",
    )
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Ref the versions are compared against, e.g. origin/main.",
    )
    args = parser.parse_args(argv)
    root = posixpath.normpath(args.modules_root.strip("/") or ".")

    try:
        return check(args.base_ref, root)
    except CheckError as exc:
        print(f"::error title=Version bump::{exc}")
        summary("### Version bump")
        summary("")
        summary(f"The check could not run: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
