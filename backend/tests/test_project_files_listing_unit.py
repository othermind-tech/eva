"""
V6.60 -- GET /projects/{id}/files: recursive listing, off the event loop.

The endpoint lists a project's workspace RECURSIVELY (the Files tab shows the whole tree;
for an external project that is the user's real codebase). It used to do the whole walk
-- rglob + sort + resolve + stat per file -- directly on the event loop, so listing a big
codebase froze every user's requests. It now runs in a worker thread.

Recursion and the exact result are the point of the endpoint, so the main guarantee here
is PARITY: for several project layouts the new handler must return exactly what the
original code did. `_reference_listing` below is that original code, extracted verbatim
from git history (commit d5aa305), not re-typed.
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import projects as P


def _reference_listing(workspace_root, uploads_dir, knowledge_dir, is_external):
    """The pre-V6.60 body of GET /projects/{id}/files, extracted verbatim from git (commit d5aa305)."""
    eva_dir = workspace_root / ".eva"

    files: list[dict] = []
    seen_abs: set[str] = set()  # absolute paths already added (dedup guard)

    def _stat_entry(entry: Path, rel_path: str, source: str, purpose: str) -> dict:
        stat = entry.stat()
        return {
            "path":         rel_path,
            "source":       source,
            "purpose":      purpose,
            "size":         stat.st_size,
            "modified_at":  datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "storage_path": str(entry.resolve()),
        }

    # ── 1. Knowledge dir (RAG-indexed docs) ──────────────────────────────────
    #       Scanned first so knowledge files appear at the top of the listing
    #       and are correctly tagged even when knowledge_dir is inside workspace_root.
    if knowledge_dir.exists():
        for entry in sorted(knowledge_dir.rglob("*")):
            if not entry.is_file():
                continue
            abs_str = str(entry.resolve())
            if abs_str in seen_abs:
                continue
            rel_path = entry.relative_to(knowledge_dir).as_posix()
            files.append(_stat_entry(entry, rel_path, "knowledge", "knowledge"))
            seen_abs.add(abs_str)

    # ── 2. Scan workspace directory ──────────────────────────────────────────
    if workspace_root.exists():
        for entry in sorted(workspace_root.rglob("*")):
            if not entry.is_file():
                continue

            # Exclude .eva/ (EVA internal metadata)
            try:
                entry.relative_to(eva_dir)
                continue
            except ValueError:
                pass

            abs_str = str(entry.resolve())
            # Knowledge files already in seen_abs — skip via dedup guard
            if abs_str in seen_abs:
                continue

            rel_path = entry.relative_to(workspace_root).as_posix()

            # Files inside the managed uploads subdir are tagged "uploaded";
            # everything else (agent output, user files, existing codebase) is "workspace".
            source = "workspace"
            if not is_external and uploads_dir.exists():
                try:
                    entry.relative_to(uploads_dir)
                    source = "uploaded"
                except ValueError:
                    pass

            files.append(_stat_entry(entry, rel_path, source, "workspace"))
            seen_abs.add(abs_str)

    # ── 3. For external workspaces, uploads_dir is outside workspace_root ────
    #       Scan it separately so uploaded files still appear in the listing.
    if is_external and uploads_dir.exists():
        for entry in sorted(uploads_dir.rglob("*")):
            if not entry.is_file():
                continue
            abs_str = str(entry.resolve())
            if abs_str in seen_abs:
                continue
            rel_path = entry.relative_to(uploads_dir).as_posix()
            files.append(_stat_entry(entry, rel_path, "uploaded", "workspace"))
            seen_abs.add(abs_str)

    # Sort: knowledge first, then uploaded, then workspace — alphabetically within each group
    _sort_order = {"knowledge": 0, "uploaded": 1, "workspace": 2}
    files.sort(key=lambda f: (_sort_order.get(f["source"], 3), f["path"]))

    return {
        "files":          files,
        "total":          len(files),
        "workspace_root": str(workspace_root),
    }



def _w(root, rel, text="x"):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class _Layout:
    """A project on disk. `external=True` puts the workspace outside the managed dir."""

    def __init__(self, external=False):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.managed = base / "managed"
        self.managed.mkdir()
        self.uploads = self.managed / "files"
        self.knowledge = self.managed / "knowledge"
        self.external = external
        self.workspace = (base / "codebase") if external else self.managed
        self.workspace.mkdir(exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()

    async def call(self, **settings):
        project = NS(workspace_path=str(self.workspace))
        repo_get = AsyncMock(return_value=project)
        with patch.object(P.ProjectRepo, "get", repo_get), \
             patch.object(P, "AsyncSessionLocal", MagicMock(return_value=_Ctx())), \
             patch("utils.workspace.get_project_dir", lambda u, p: self.managed), \
             patch("utils.workspace.get_project_knowledge_dir", lambda u, p: self.knowledge), \
             patch.object(P, "_is_external_workspace", lambda wp: self.external):
            overrides = patch.multiple(P, **settings) if settings else patch.dict(os.environ, {})
            with overrides:
                return await P.list_project_files("proj-1", auth={"user_id": "u"})

    def reference(self):
        return _reference_listing(self.workspace, self.uploads, self.knowledge, self.external)


class _Ctx:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *a):
        return False


def _populate_managed(lay):
    _w(lay.knowledge, "spec.pdf", "k1")
    _w(lay.knowledge, "notes/design.md", "k2")
    _w(lay.uploads, "report.csv", "u1")
    _w(lay.uploads, "deep/er/data.json", "u2")
    _w(lay.workspace, "src/main.py", "w1")
    _w(lay.workspace, "src/pkg/mod.py", "w2")
    _w(lay.workspace, "README.md", "w3")
    _w(lay.workspace, ".eva/internal.json", "hidden")            # must never be listed
    _w(lay.workspace, ".hidden_dotfile", "w4")                   # dotfiles ARE listed


def _populate_external(lay):
    _w(lay.knowledge, "spec.pdf", "k1")
    _w(lay.uploads, "report.csv", "u1")                           # lives OUTSIDE the external workspace
    _w(lay.workspace, "src/app.py", "a")
    _w(lay.workspace, "src/lib/util.py", "b")
    _w(lay.workspace, "docs/readme.md", "c")
    _w(lay.workspace, ".git/config", "git")                       # listed today -- behaviour is unchanged
    _w(lay.workspace, "node_modules/pkg/index.js", "js")          # listed today -- behaviour is unchanged
    _w(lay.workspace, ".eva/meta.json", "hidden")


def _strip_mtime(listing):
    return listing


class ParityTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_same(self, lay):
        new = await lay.call()
        ref = lay.reference()
        self.assertEqual(new["files"], ref["files"])
        self.assertEqual(new["total"], ref["total"])
        self.assertEqual(new["workspace_root"], ref["workspace_root"])
        self.assertFalse(new.get("truncated"))          # raw dict: key only present when truncated
        return new

    async def test_managed_project_matches_the_original_exactly(self):
        lay = _Layout(external=False)
        try:
            _populate_managed(lay)
            new = await self._assert_same(lay)
            by_source = {s: [f["path"] for f in new["files"] if f["source"] == s] for s in ("knowledge", "uploaded", "workspace")}
            self.assertEqual(by_source["knowledge"], ["notes/design.md", "spec.pdf"])
            # managed project: uploads/ sits inside the workspace, so those files keep their folder prefix
            self.assertEqual(by_source["uploaded"], ["files/deep/er/data.json", "files/report.csv"])
            self.assertIn("src/pkg/mod.py", by_source["workspace"])
        finally:
            lay.cleanup()

    async def test_external_workspace_matches_the_original_exactly(self):
        lay = _Layout(external=True)
        try:
            _populate_external(lay)
            await self._assert_same(lay)
        finally:
            lay.cleanup()

    async def test_empty_and_missing_directories_match(self):
        lay = _Layout(external=False)
        try:
            await self._assert_same(lay)                       # nothing at all
            lay2 = _Layout(external=True)
            try:
                import shutil
                shutil.rmtree(lay2.workspace)                  # workspace path that no longer exists
                await self._assert_same(lay2)
            finally:
                lay2.cleanup()
        finally:
            lay.cleanup()

    async def test_a_file_in_both_knowledge_and_workspace_is_listed_once_as_knowledge(self):
        lay = _Layout(external=False)               # knowledge/ sits inside the managed workspace
        try:
            _w(lay.knowledge, "a.md", "k")
            new = await self._assert_same(lay)
            self.assertEqual([(f["path"], f["source"]) for f in new["files"]], [("a.md", "knowledge")])
        finally:
            lay.cleanup()


class RecursionIsPreservedTests(unittest.IsolatedAsyncioTestCase):
    """The recursive listing IS the feature; these would fail if it were flattened or pruned."""

    async def test_deeply_nested_files_are_listed(self):
        lay = _Layout(external=True)
        try:
            deep = "/".join(f"d{i}" for i in range(12)) + "/leaf.txt"
            _w(lay.workspace, deep, "x")
            _w(lay.workspace, "top.txt", "x")
            new = await lay.call()
            paths = [f["path"] for f in new["files"] if f["source"] == "workspace"]
            self.assertEqual(sorted(paths), sorted([deep, "top.txt"]))
        finally:
            lay.cleanup()

    async def test_eva_internal_dir_stays_hidden_and_dotfiles_and_dot_dirs_stay_listed(self):
        lay = _Layout(external=True)
        try:
            _populate_external(lay)
            paths = [f["path"] for f in (await lay.call())["files"]]
            self.assertNotIn(".eva/meta.json", paths)
            self.assertIn(".git/config", paths)                      # unchanged behaviour: not pruned
            self.assertIn("node_modules/pkg/index.js", paths)        # unchanged behaviour: not pruned
        finally:
            lay.cleanup()

    async def test_entries_carry_the_same_fields_as_before(self):
        lay = _Layout(external=False)
        try:
            _w(lay.workspace, "a.txt", "hello")
            entry = (await lay.call())["files"][0]
            self.assertEqual(set(entry), {"path", "source", "purpose", "size", "modified_at", "storage_path"})
            self.assertEqual(entry["size"], 5)
        finally:
            lay.cleanup()


class EventLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_slow_walk_does_not_block_the_event_loop(self):
        """Each Path.is_file() costs 3 ms of 'disk' (400 entries ~ 1.2 s). Inline, that is a
        1.2 s freeze; in a thread the loop keeps ticking."""
        lay = _Layout(external=True)
        try:
            for i in range(400):
                _w(lay.workspace, f"d{i % 20}/f{i}.txt", "x")
            real_is_file = Path.is_file

            def slow_is_file(self_, *a, **k):
                time.sleep(0.003)
                return real_is_file(self_, *a, **k)

            gaps, done = [], False

            async def ticker():
                last = time.monotonic()
                while not done:
                    await asyncio.sleep(0.02)
                    now = time.monotonic()
                    gaps.append(now - last)
                    last = now

            with patch.object(Path, "is_file", slow_is_file):
                tk = asyncio.ensure_future(ticker())
                try:
                    result = await lay.call()
                finally:
                    done = True
                    await tk
            self.assertEqual(result["total"], 400)
            self.assertLess(max(gaps), 0.3, f"event loop stalled {max(gaps):.2f}s during the listing")
            self.assertGreater(len(gaps), 20)
        finally:
            lay.cleanup()

    async def test_cancelling_the_request_stops_the_walk(self):
        lay = _Layout(external=True)
        try:
            for i in range(600):
                _w(lay.workspace, f"f{i}.txt", "x")
            seen = {"n": 0}
            real_is_file = Path.is_file

            def counting_slow_is_file(self_, *a, **k):
                seen["n"] += 1
                time.sleep(0.005)
                return real_is_file(self_, *a, **k)

            with patch.object(Path, "is_file", counting_slow_is_file):
                task = asyncio.ensure_future(lay.call())
                await asyncio.sleep(0.25)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                at_cancel = seen["n"]
                await asyncio.sleep(0.4)
                after = seen["n"]
            self.assertGreater(at_cancel, 0)
            self.assertLessEqual(after - at_cancel, 3, "the walk kept going after the request was cancelled")
            self.assertLess(after, 600)
        finally:
            lay.cleanup()


class CeilingTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_listing_is_cut_at_the_ceiling_and_says_so(self):
        lay = _Layout(external=True)
        try:
            for i in range(20):
                _w(lay.workspace, f"f{i:02d}.txt", "x")
            new = await lay.call(PROJECT_FILES_MAX_WORKSPACE_ENTRIES=5)
            self.assertEqual(new["total"], 5)
            self.assertEqual([f["path"] for f in new["files"]], [f"f{i:02d}.txt" for i in range(5)])
            self.assertTrue(new["truncated"])
            self.assertIn("5", new["truncated_message"])
        finally:
            lay.cleanup()

    async def test_knowledge_and_uploaded_files_are_never_cut(self):
        lay = _Layout(external=False)
        try:
            for i in range(4):
                _w(lay.knowledge, f"k{i}.md", "k")
            for i in range(3):
                _w(lay.uploads, f"u{i}.csv", "u")
            for i in range(30):
                _w(lay.workspace, f"src/w{i:02d}.py", "w")
            new = await lay.call(PROJECT_FILES_MAX_WORKSPACE_ENTRIES=2)
            counts = {s: sum(1 for f in new["files"] if f["source"] == s) for s in ("knowledge", "uploaded", "workspace")}
            self.assertEqual(counts, {"knowledge": 4, "uploaded": 3, "workspace": 2})
            self.assertTrue(new["truncated"])
        finally:
            lay.cleanup()

    async def test_external_projects_uploads_outside_the_workspace_are_never_cut(self):
        lay = _Layout(external=True)
        try:
            for i in range(10):
                _w(lay.workspace, f"w{i}.py", "w")
            _w(lay.uploads, "report.csv", "u")
            new = await lay.call(PROJECT_FILES_MAX_WORKSPACE_ENTRIES=3)
            self.assertIn("report.csv", [f["path"] for f in new["files"] if f["source"] == "uploaded"])
        finally:
            lay.cleanup()

    async def test_under_the_ceiling_nothing_is_flagged(self):
        lay = _Layout(external=True)
        try:
            for i in range(5):
                _w(lay.workspace, f"f{i}.txt", "x")
            new = await lay.call(PROJECT_FILES_MAX_WORKSPACE_ENTRIES=5)         # exactly at the ceiling
            self.assertEqual(new["total"], 5)
            self.assertFalse(new.get("truncated"))
            self.assertIsNone(new.get("truncated_message"))
        finally:
            lay.cleanup()

    async def test_the_default_ceiling_is_far_above_ordinary_projects(self):
        from config import settings
        self.assertGreaterEqual(settings.PROJECT_FILES_MAX_WORKSPACE_ENTRIES, 10000)


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_project_is_404(self):
        from fastapi import HTTPException
        with patch.object(P.ProjectRepo, "get", AsyncMock(return_value=None)), \
             patch.object(P, "AsyncSessionLocal", MagicMock(return_value=_Ctx())):
            with self.assertRaises(HTTPException) as cm:
                await P.list_project_files("nope", auth={"user_id": "u"})
        self.assertEqual(cm.exception.status_code, 404)

    def test_response_model_carries_the_new_fields(self):
        r = P.ListProjectFilesResponse(files=[], total=0, workspace_root="/x")
        self.assertFalse(r.truncated)
        self.assertIsNone(r.truncated_message)
        r2 = P.ListProjectFilesResponse(files=[], total=0, workspace_root="/x", truncated=True, truncated_message="m")
        self.assertTrue(r2.truncated)


class GuardTests(unittest.TestCase):
    def test_the_async_handler_does_no_directory_walking_itself(self):
        import ast
        tree = ast.parse(Path(P.__file__).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_project_files":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and ast.unparse(sub.func).rsplit(".", 1)[-1] in ("rglob", "glob", "iterdir", "walk"):
                        offenders.append(ast.unparse(sub.func))
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# Speed-up (resolve each DIRECTORY once instead of every file twice). The output
# must stay identical to the original in every case where resolve() is not trivial:
# symlinks, junctions, odd names, unreadable directories.
# ---------------------------------------------------------------------------
import subprocess
import threading


def _symlink(target, link, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
        return True
    except (OSError, NotImplementedError):
        return False


def _junction(target, link):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    return r.returncode == 0


class HardCaseParityTests(unittest.IsolatedAsyncioTestCase):
    async def _same(self, lay):
        new = await lay.call()
        ref = lay.reference()
        self.assertEqual(new["files"], ref["files"])
        return new

    async def test_file_symlinks_resolve_to_their_targets_exactly_as_before(self):
        lay = _Layout(external=True)
        try:
            _w(lay.workspace, "real/data.txt", "d")
            if not _symlink(lay.workspace / "real" / "data.txt", lay.workspace / "alias.txt"):
                self.skipTest("cannot create symlinks on this machine")
            new = await self._same(lay)
            alias = next(f for f in new["files"] if f["path"] == "alias.txt")
            self.assertTrue(alias["storage_path"].endswith(os.path.join("real", "data.txt")))   # points at the target
        finally:
            lay.cleanup()

    async def test_a_symlink_chain_resolves_all_the_way(self):
        lay = _Layout(external=True)
        try:
            _w(lay.workspace, "real/data.txt", "d")
            if not (_symlink(lay.workspace / "real" / "data.txt", lay.workspace / "hop1.txt")
                    and _symlink(lay.workspace / "hop1.txt", lay.workspace / "hop2.txt")):
                self.skipTest("cannot create symlinks on this machine")
            await self._same(lay)
        finally:
            lay.cleanup()

    async def test_directory_symlinks_behave_as_before(self):
        lay = _Layout(external=True)
        try:
            _w(lay.workspace, "real/data.txt", "d")
            if not _symlink(lay.workspace / "real", lay.workspace / "linkdir", is_dir=True):
                self.skipTest("cannot create symlinks on this machine")
            await self._same(lay)
        finally:
            lay.cleanup()

    @unittest.skipUnless(os.name == "nt", "directory junctions are Windows-only")
    async def test_junctions_are_followed_exactly_as_before(self):
        lay = _Layout(external=True)
        try:
            _w(lay.workspace, "real/data.txt", "d")
            _w(lay.workspace, "real/deeper/more.txt", "m")
            if not _junction(lay.workspace / "real", lay.workspace / "junc"):
                self.skipTest("cannot create junctions on this machine")
            new = await self._same(lay)
            self.assertIn("junc/data.txt", [f["path"] for f in new["files"]])
        finally:
            lay.cleanup()

    async def test_a_symlinked_workspace_root_behaves_as_before(self):
        lay = _Layout(external=True)
        try:
            _w(lay.workspace, "a/b.txt", "x")
            link = lay.workspace.parent / "ws_link"
            if not _symlink(lay.workspace, link, is_dir=True):
                self.skipTest("cannot create symlinks on this machine")
            lay.workspace = link
            await self._same(lay)
        finally:
            lay.cleanup()

    async def test_a_file_reachable_via_knowledge_and_via_a_symlink_is_deduplicated_like_before(self):
        lay = _Layout(external=False)
        try:
            _w(lay.knowledge, "a.md", "k")
            if not _symlink(lay.knowledge / "a.md", lay.workspace / "alias.md"):
                self.skipTest("cannot create symlinks on this machine")
            new = await self._same(lay)
            self.assertEqual(len([f for f in new["files"] if f["storage_path"].endswith("a.md")]), 1)
        finally:
            lay.cleanup()

    async def test_unusual_file_names_match(self):
        lay = _Layout(external=True)
        try:
            for rel in ("Äpfel Ünïcode.txt", "UPPER.TXT", "MiXed Case/Sub Dir/File (1).py", " lead-space/x y.txt",
                        "dots.in.name.tar.gz", "日本語/ファイル.md", "a" * 120 + ".txt"):
                _w(lay.workspace, rel, "x")
            await self._same(lay)
        finally:
            lay.cleanup()

    async def test_managed_project_with_uploads_inside_the_workspace_matches(self):
        lay = _Layout(external=False)
        try:
            _populate_managed(lay)
            await self._same(lay)
        finally:
            lay.cleanup()


class ResolveCacheTests(unittest.TestCase):
    def _tree(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        _w(root, "a/f1.txt")
        _w(root, "a/f2.txt")
        _w(root, "a/b/f3.txt")
        _w(root, "top.txt")
        return tmp, root

    def test_equals_entry_resolve_for_every_file(self):
        tmp, root = self._tree()
        try:
            cache = P._ResolveCache()
            for e in root.rglob("*"):
                if e.is_file():
                    self.assertEqual(cache.resolve(e), str(e.resolve()))
        finally:
            tmp.cleanup()

    def test_resolves_each_directory_once_not_each_file(self):
        tmp, root = self._tree()
        try:
            cache = P._ResolveCache()
            files = [e for e in root.rglob("*") if e.is_file()]
            real = Path.resolve
            calls = []

            def counting(self_, *a, **k):
                calls.append(1)
                return real(self_, *a, **k)

            with patch.object(Path, "resolve", counting):
                for e in files + files:                     # asking twice must not resolve again either
                    cache.resolve(e)
            self.assertEqual(len(files), 4)
            self.assertLessEqual(len(calls), 3, f"{len(calls)} resolve() calls for 4 files in 3 directories")
        finally:
            tmp.cleanup()

    def test_symlinked_files_fall_back_to_the_per_entry_resolve(self):
        tmp, root = self._tree()
        try:
            if not _symlink(root / "top.txt", root / "a" / "link.txt"):
                self.skipTest("cannot create symlinks on this machine")
            cache = P._ResolveCache()
            link = root / "a" / "link.txt"
            self.assertEqual(cache.resolve(link), str(link.resolve()))
            naive = os.path.join(str((root / "a").resolve()), "link.txt")
            self.assertNotEqual(cache.resolve(link), naive)         # NOT the naive directory join
        finally:
            tmp.cleanup()

    def test_an_unreadable_directory_falls_back_to_the_per_entry_resolve(self):
        tmp, root = self._tree()
        try:
            cache = P._ResolveCache()

            def failing_scandir(path, *a, **k):
                raise PermissionError("denied")

            files = [e for e in root.rglob("*") if e.is_file()]
            with patch.object(P.os, "scandir", failing_scandir):
                got = [cache.resolve(e) for e in files]
            self.assertEqual(sorted(got), sorted(str(e.resolve()) for e in files))
        finally:
            tmp.cleanup()

    def test_whole_listing_resolves_far_fewer_than_twice_per_file(self):
        """Regression guard for the speed-up: the original made 2 resolve() calls per file."""
        lay = _Layout(external=True)
        try:
            for d in range(10):
                for f in range(30):
                    _w(lay.workspace, f"d{d}/f{f}.txt", "x")
            real = Path.resolve
            calls = []

            def counting(self_, *a, **k):
                calls.append(1)
                return real(self_, *a, **k)

            with patch.object(Path, "resolve", counting):
                files, _ = P._collect_project_files(lay.workspace, lay.uploads, lay.knowledge, True,
                                                    threading.Event(), 50000)
            self.assertEqual(len(files), 300)
            self.assertLess(len(calls), 60, f"{len(calls)} resolve() calls for 300 files (the original made 600)")
        finally:
            lay.cleanup()


if __name__ == "__main__":
    unittest.main()
