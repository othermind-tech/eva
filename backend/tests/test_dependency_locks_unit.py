"""
Dependency lock guards.

requirements.txt lists the direct dependencies; requirements.lock pins the full,
hash-checked set that builds and releases use. If the two
drift, a build silently ships something nobody reviewed. These tests fail when:

  * a direct requirement is missing from the lock, or its locked version does not satisfy
    the requirement (someone edited requirements.txt and forgot to re-lock);
  * the lock still carries a package that was only there for a requirement that is gone;
  * a locked runtime package declares a copyleft licence (GPL/AGPL/LGPL/SSPL/non-commercial)
    that the open-source release must not ship.

Re-lock with the command on the first line of requirements.lock.
"""
import os
import re
import sys
import unittest
from importlib import metadata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

BACKEND = Path(__file__).resolve().parent.parent


def parse_direct(txt: str) -> dict:
    """requirements.txt text -> {canonical name: Requirement}"""
    out = {}
    for raw in txt.splitlines():
        line = raw.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        req = Requirement(line)
        out[canonicalize_name(req.name)] = req
    return out


def parse_lock(txt: str):
    """lock text -> ({name: version}, {name: [via lines]})  (names canonical)"""
    pins, via = {}, {}
    current = None
    in_via = False
    for line in txt.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s;\\]+)", line)
        if not m:
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s+@\s+(\S+)", line)      # URL pin: name @ https://...
        if m:
            current = canonicalize_name(m.group(1).split("[")[0])
            pins[current] = m.group(2)
            via[current] = []
            in_via = False
            continue
        if current and line.lstrip().startswith("# via"):
            in_via = True
            rest = line.split("via", 1)[1].strip()
            if rest:
                via[current].append(rest)
            continue
        if current and in_via and line.lstrip().startswith("#"):
            via[current].append(line.lstrip("# ").strip())
        elif line.strip() and not line.lstrip().startswith(("#", "--hash")):
            in_via = False
    return pins, via


def lock_problems(requirements_txt: str, lock_txt: str, via_marker: str) -> list:
    direct = parse_direct(requirements_txt)
    pins, via = parse_lock(lock_txt)
    problems = []
    for name, req in sorted(direct.items()):
        if name not in pins:
            problems.append(f"{req.name}: required by requirements but missing from the lock")
            continue
        if req.url:                                    # direct URL requirement: the lock must pin the same URL
            if pins[name] != req.url:
                problems.append(f"{req.name}: locked URL {pins[name]} differs from requirement URL {req.url}")
        elif req.specifier and not req.specifier.contains(Version(pins[name]), prereleases=True):
            problems.append(f"{req.name}: locked {pins[name]} does not satisfy '{req.specifier}'")
    for name, lines in sorted(via.items()):
        if any(via_marker in l for l in lines) and name not in direct:
            problems.append(f"{name}: in the lock only for a requirement that is no longer listed")
    return problems


class TestLocksMatchRequirements(unittest.TestCase):
    def _check(self, req_file, lock_file, marker):
        problems = lock_problems(
            (BACKEND / req_file).read_text(encoding="utf-8"),
            (BACKEND / lock_file).read_text(encoding="utf-8"),
            marker,
        )
        self.assertEqual(problems, [], f"{lock_file} is out of date with {req_file}; re-lock it:\n" + "\n".join(problems))

    def test_runtime_lock_matches_requirements_txt(self):
        self._check("requirements.txt", "requirements.lock", "-r requirements.txt")

    def test_dev_lock_matches_requirements_dev_txt(self):
        self._check("requirements-dev.txt", "requirements-dev.lock", "-r requirements-dev.txt")

    def test_lock_files_carry_hashes_and_the_python_target(self):
        head = (BACKEND / "requirements.lock").read_text(encoding="utf-8")[:400]
        self.assertIn("--generate-hashes", head)
        self.assertIn("--universal", head)
        self.assertIn("--python-version 3.11", head)

    def test_litellm_is_pinned_exactly(self):
        # Supply-chain rule: never a range.
        req = parse_direct((BACKEND / "requirements.txt").read_text(encoding="utf-8"))["litellm"]
        self.assertTrue(str(req.specifier).startswith("=="), f"litellm must be pinned with ==, got {req.specifier}")


class TestCheckerCatchesDrift(unittest.TestCase):
    """The checker itself: each kind of drift must be reported (so a green result means something)."""

    LOCK = (
        "# autogenerated\n"
        "foo==1.2.0 \\\n    --hash=sha256:aa\n    # via -r requirements.txt\n"
        "bar==2.0.0 \\\n    --hash=sha256:bb\n    # via -r requirements.txt\n"
        "baz==3.0.0 \\\n    --hash=sha256:cc\n    # via\n    #   foo\n"
    )

    def test_clean_when_in_step(self):
        self.assertEqual(lock_problems("foo>=1.0\nbar==2.0.0\n", self.LOCK, "-r requirements.txt"), [])

    def test_new_requirement_not_locked(self):
        p = lock_problems("foo>=1.0\nbar==2.0.0\nnew>=1\n", self.LOCK, "-r requirements.txt")
        self.assertTrue(any("new" in x and "missing" in x for x in p), p)

    def test_requirement_tightened_beyond_the_lock(self):
        p = lock_problems("foo>=1.5\nbar==2.0.0\n", self.LOCK, "-r requirements.txt")
        self.assertTrue(any("foo" in x and "does not satisfy" in x for x in p), p)

    def test_requirement_removed_but_lock_keeps_it(self):
        p = lock_problems("foo>=1.0\n", self.LOCK, "-r requirements.txt")
        self.assertTrue(any("bar" in x and "no longer listed" in x for x in p), p)

    def test_transitive_package_is_not_flagged(self):
        self.assertEqual(lock_problems("foo>=1.0\nbar==2.0.0\n", self.LOCK, "-r requirements.txt"), [])

    def test_url_requirement_must_match_the_locked_url(self):
        lock = "m-x @ https://example.com/m_x-1.whl \\\n    --hash=sha256:aa\n    # via -r requirements.txt\n"
        self.assertEqual(lock_problems("m_x @ https://example.com/m_x-1.whl\n", lock, "-r requirements.txt"), [])
        p = lock_problems("m_x @ https://example.com/m_x-2.whl\n", lock, "-r requirements.txt")
        self.assertTrue(any("differs" in x for x in p), p)

    def test_extras_and_markers_and_comments_parse(self):
        d = parse_direct("sqlalchemy[asyncio]>=2.0.0   # note\nuvicorn[standard]>=0.30 ; sys_platform != 'x'\n# c\n")
        self.assertEqual(set(d), {"sqlalchemy", "uvicorn"})


COPYLEFT = re.compile(
    r"(AGPL|\bGPL|General Public|LGPL|Lesser|SSPL|Server Side Public|BUSL|Business Source|"
    r"Commons Clause|CC-BY-NC|Non-?Commercial|Elastic License)", re.I)


class TestNoCopyleftInRuntimeLock(unittest.TestCase):
    def test_locked_packages_have_no_copyleft_licence(self):
        pins, _ = parse_lock((BACKEND / "requirements.lock").read_text(encoding="utf-8"))
        checked, flagged = 0, []
        for name in pins:
            try:
                md = metadata.distribution(name).metadata
            except metadata.PackageNotFoundError:
                continue          # not installed in this environment (platform-specific); cannot inspect
            checked += 1
            lic = (md.get("License-Expression") or md.get("License") or "")
            lic = lic.splitlines()[0][:80] if lic else ""
            classifiers = [c for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
            blob = " | ".join([lic] + classifiers)
            if COPYLEFT.search(blob):
                flagged.append(f"{name} {pins[name]}: {blob[:110]}")
        if checked == 0:
            self.skipTest("none of the locked packages are installed here; install the lock to run this check")
        self.assertEqual(flagged, [], "copyleft-licensed packages in requirements.lock:\n" + "\n".join(flagged))

    def test_the_licence_matcher_recognises_the_usual_spellings(self):
        for s in ("GNU Affero General Public License v3", "GPLv3", "GNU Lesser General Public License v2 (LGPLv2)",
                  "LGPL-3.0-or-later", "Server Side Public License", "CC-BY-NC-4.0", "Business Source License 1.1"):
            self.assertTrue(COPYLEFT.search(s), s)
        for s in ("MIT License", "Apache-2.0", "BSD-3-Clause", "ISC", "Mozilla Public License 2.0 (MPL 2.0)",
                  "Python Software Foundation License"):
            self.assertFalse(COPYLEFT.search(s), s)


if __name__ == "__main__":
    unittest.main()
