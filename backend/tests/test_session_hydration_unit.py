# tests/test_session_hydration_unit.py
"""
core/auth/hydration.py::build_prefs_dict() must carry every stored preference into the
in-memory session.

Found live (2026-09-21): POST /set-session stored max_step_extensions=6 (in the DB and
in memory, GET /session returned 6), but a running execution's step-limit gate logged
extensions_max=2. build_prefs_dict() never copied that column, and the worker rebuilds
the session from the DB at every execution start (and login), so
hydrate_user_session_from_db() fell back to its default of 2 each time -- the setting
could never take effect.

Run with:
    pytest tests/test_session_hydration_unit.py -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

# Columns that are deliberately not part of the in-memory session.
NOT_HYDRATED = {"user_id", "updated_at", "system_prompt"}  # system_prompt: per-conversation since B-054


def _prefs(**overrides):
    from core.db.models import UserPreference
    values = {c.name: None for c in UserPreference.__table__.columns}
    values.update(provider="ollama_cloud", model="gpt-oss:120b")
    values.update(overrides)
    return SimpleNamespace(**values)


class TestMaxStepExtensionsIsHydrated(unittest.TestCase):

    def test_stored_value_is_carried_into_the_prefs_dict(self):
        from core.auth.hydration import build_prefs_dict
        self.assertEqual(build_prefs_dict(_prefs(max_step_extensions=6))["max_step_extensions"], 6)

    def test_zero_is_preserved_it_disables_the_gate(self):
        from core.auth.hydration import build_prefs_dict
        self.assertEqual(build_prefs_dict(_prefs(max_step_extensions=0))["max_step_extensions"], 0)

    def test_missing_value_falls_back_to_the_default(self):
        from core.auth.hydration import build_prefs_dict
        from config.settings import DEFAULT_SESSION
        self.assertEqual(build_prefs_dict(_prefs(max_step_extensions=None))["max_step_extensions"],
                         DEFAULT_SESSION["max_step_extensions"])

    def test_the_value_survives_the_whole_login_and_execution_start_path(self):
        from config.settings import _USER_SESSIONS, get_user_session, hydrate_user_session_from_db
        from core.auth.hydration import build_prefs_dict
        user = "hydration-test-user"
        try:
            hydrate_user_session_from_db(user, build_prefs_dict(_prefs(max_step_extensions=6)))
            self.assertEqual(get_user_session(user)["max_step_extensions"], 6)
        finally:
            _USER_SESSIONS.pop(user, None)


class TestNoStoredPreferenceIsSilentlyDropped(unittest.TestCase):

    def test_every_preference_column_is_hydrated(self):
        from core.auth.hydration import build_prefs_dict
        from core.db.models import UserPreference
        columns = {c.name for c in UserPreference.__table__.columns} - NOT_HYDRATED
        missing = columns - set(build_prefs_dict(_prefs()))
        self.assertEqual(missing, set(),
                         f"UserPreference column(s) never reach the session: {sorted(missing)} -- add them to "
                         "build_prefs_dict() (or to NOT_HYDRATED here, with a reason)")


if __name__ == "__main__":
    unittest.main()
