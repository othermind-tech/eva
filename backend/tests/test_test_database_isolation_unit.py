"""
The unit suite must never touch the developer's real database.

Several tests round-trip through AsyncSessionLocal. They used to hit ./eva.db (the
developer's dev database) and failed with "no such table" on a clean checkout.
tests/conftest.py now points DATABASE_URL at a private temp SQLite file and builds
it with `alembic upgrade head` before the first test. These tests pin that.
"""
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _sqlite_path(url: str) -> Path:
    # sqlite+aiosqlite:///C:/Users/x/eva_test.db  ->  C:/Users/x/eva_test.db
    return Path(url.split(":///", 1)[1])


@unittest.skipIf(os.environ.get("EVA_TEST_DATABASE_URL"), "explicit test database requested")
class TestDatabaseIsolation(unittest.TestCase):
    def test_settings_database_is_not_the_developer_database(self):
        from config import settings

        self.assertTrue(settings.DATABASE_URL.startswith("sqlite+aiosqlite:///"))
        path = _sqlite_path(settings.DATABASE_URL)
        self.assertTrue(path.is_absolute(), "the default './eva.db' is relative to the backend folder")
        self.assertNotEqual(path.resolve(), (BACKEND_ROOT / "eva.db").resolve())
        self.assertIn("eva_test_db_", str(path))

    def test_the_engine_the_app_uses_is_the_same_private_database(self):
        from config import settings
        from core.db.session import engine

        self.assertEqual(
            Path(engine.url.database).resolve(), _sqlite_path(settings.DATABASE_URL).resolve()
        )

    def test_private_database_was_built_from_the_migrations(self):
        from config import settings

        con = sqlite3.connect(str(_sqlite_path(settings.DATABASE_URL)))
        try:
            tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            head = con.execute("select version_num from alembic_version").fetchone()[0]
        finally:
            con.close()
        for needed in ("daemon_configs", "background_processes", "conversations", "users"):
            self.assertIn(needed, tables)

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        self.assertEqual(head, ScriptDirectory.from_config(cfg).get_current_head())


if __name__ == "__main__":
    unittest.main()
