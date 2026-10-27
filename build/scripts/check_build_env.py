"""Verify the disposable build environment matches the lock files (run by build-backend.ps1).

Usage: python check_build_env.py <requirements.lock> [<requirements-build.lock> ...]

Fails (exit 1) when:
  * the interpreter is not Python 3.11
  * an installed package differs from the version pinned in a lock file
  * a pinned package that applies to this platform is missing
  * a key security/compat pin drifted (litellm, mcp, pypdf)
  * PyMuPDF (AGPL) is present or importable
"""
import importlib.util
import re
import sys
from importlib import metadata

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

# litellm is pinned exactly in requirements.txt after the March 2026 PyPI compromise.
KEY_PINS = {"litellm": "==1.106.0", "pypdf": "==6.21.1"}
KEY_FLOORS = {"mcp": (2, 2)}
FORBIDDEN = ["pymupdf", "pymupdf4llm", "pymupdf-layout"]


def pinned_requirements(path):
    """Yield Requirement objects for each pinned entry in a hash-locked file."""
    entry = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line[0] in " \t":
                continue  # --hash= continuation line
            text = line.rstrip(" \\")
            try:
                yield Requirement(text)
            except Exception:
                print(f"  warning: could not parse lock line: {text}")


def main(lock_paths):
    problems = []

    if sys.version_info[:2] != (3, 11):
        problems.append(f"Python {sys.version_info[0]}.{sys.version_info[1]} (the lock is for 3.11)")

    env = default_environment()
    checked = 0
    for lock in lock_paths:
        for req in pinned_requirements(lock):
            if req.marker is not None and not req.marker.evaluate(env):
                continue
            name = canonicalize_name(req.name)
            # URL-pinned entries (the spaCy model wheel) have no version specifier.
            pinned = next(iter(req.specifier)).version if req.specifier else None
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                problems.append(f"{name} is in the lock but not installed")
                continue
            checked += 1
            if pinned is not None and installed != pinned:
                problems.append(f"{name}: installed {installed}, lock pins {pinned}")

    for name, spec in KEY_PINS.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name} is not installed")
            continue
        if installed != spec.lstrip("="):
            problems.append(f"{name} {installed} does not match the expected {spec}")

    for name, floor in KEY_FLOORS.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name} is not installed")
            continue
        parts = tuple(int(p) for p in re.findall(r"\d+", installed)[:2])
        if parts < floor:
            problems.append(f"{name} {installed} is below the required >={'.'.join(map(str, floor))}")

    for name in FORBIDDEN:
        try:
            metadata.version(name)
            problems.append(f"{name} (AGPL) is installed")
        except metadata.PackageNotFoundError:
            pass
    if importlib.util.find_spec("fitz") is not None:
        problems.append("fitz (PyMuPDF, AGPL) is importable")

    if problems:
        print("Build environment check FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"Build environment OK: {checked} locked packages match, key pins hold, no PyMuPDF.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
