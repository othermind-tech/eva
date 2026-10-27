# tests/test_execution_plan_success_criteria_persistence_unit.py
"""
A-20 follow-up (2026-09-22): success_criteria only ever lived on the
in-memory plan dict for the duration of one PEV run -- gone the moment the
process moved past it, so a past run's criteria could never be inspected
later. Persisted now via a new nullable execution_plans.success_criteria
JSON column (alembic/versions/0014_add_execution_plan_success_criteria.py).

Also fixes a related, pre-existing gap found while wiring this in: on a
user-edited plan (decision="edit"), only `steps` was ever synced back to the
DB row -- an edited `goal` was silently never persisted either, the exact
same staleness class success_criteria would otherwise have had. update_steps()
now optionally accepts both.

Uses a real in-memory SQLite DB (same pattern as
TestExecutionPlanDeletedButMetricsRetained in test_features.py) rather than
mocks -- this is exactly the kind of "does the actual UPDATE statement touch
the right columns and leave the others alone" behaviour a mock would
trivially get right without proving anything.
"""

from __future__ import annotations

import unittest


class TestExecutionPlanSuccessCriteriaPersistence(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_conversation(self, db, conv_id="conv-1"):
        from core.db.models import Conversation
        db.add(Conversation(id=conv_id, user_id="u1", title="Test convo"))
        await db.commit()

    async def test_create_persists_success_criteria(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_conversation(db)
            await PlanRepo.create(
                db, execution_id="e1", conversation_id="conv-1", user_id="u1",
                goal="Build a calculator",
                success_criteria=["add(2,3)==5", "multiply(4,5)==20"],
                steps=[{"index": 0, "title": "Step", "description": "d"}],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.success_criteria, ["add(2,3)==5", "multiply(4,5)==20"])

    async def test_create_without_success_criteria_stores_null(self):
        # Backward compatible: the param is optional (older callers, or a
        # plan whose _normalise_success_criteria() somehow returned nothing --
        # shouldn't happen per its own fallback, but the DB layer itself
        # must not require it).
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_conversation(db)
            await PlanRepo.create(
                db, execution_id="e1", conversation_id="conv-1", user_id="u1",
                goal="Build a calculator",
                steps=[{"index": 0, "title": "Step", "description": "d"}],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertIsNone(plan.success_criteria)

    async def test_update_steps_with_success_criteria_updates_it(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_conversation(db)
            await PlanRepo.create(
                db, execution_id="e1", conversation_id="conv-1", user_id="u1",
                goal="Original goal", success_criteria=["original criterion"],
                steps=[{"index": 0, "title": "Step", "description": "d"}],
            )
            await PlanRepo.update_steps(
                db, execution_id="e1",
                steps=[{"index": 0, "title": "Edited step", "description": "d2"}],
                goal="Edited goal",
                success_criteria=["edited criterion 1", "edited criterion 2"],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal, "Edited goal")
            self.assertEqual(plan.success_criteria, ["edited criterion 1", "edited criterion 2"])
            self.assertEqual(plan.steps[0]["title"], "Edited step")

    async def test_update_steps_without_goal_or_criteria_leaves_them_unchanged(self):
        # The normal (non-edit) call sites -- adaptive re-planning, ordinary
        # step-progress persistence -- must NOT blank out goal/success_criteria
        # just because they didn't pass them.
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_conversation(db)
            await PlanRepo.create(
                db, execution_id="e1", conversation_id="conv-1", user_id="u1",
                goal="Original goal", success_criteria=["original criterion"],
                steps=[{"index": 0, "title": "Step", "description": "d"}],
            )
            await PlanRepo.update_steps(
                db, execution_id="e1",
                steps=[{"index": 0, "title": "Step (replanned)", "description": "d3"}],
                current_step_index=1, status="executing",
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal, "Original goal")
            self.assertEqual(plan.success_criteria, ["original criterion"])
            self.assertEqual(plan.status, "executing")
            self.assertEqual(plan.current_step_index, 1)
            self.assertEqual(plan.steps[0]["title"], "Step (replanned)")


class TestGoalVerificationPersistence(unittest.IsolatedAsyncioTestCase):
    """
    A-20 follow-up: PlanVerifierEngine's verdict/message/criteria_results
    previously only existed as an SSE-only StepProgressChunk -- confirmed
    gone from the shadow buffer shortly after a real run completed, with no
    durable trace anywhere. PlanRepo.update_goal_verification() gives it one.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_plan(self, db, execution_id="e1"):
        from core.db.models import Conversation
        from core.db.repository import PlanRepo
        db.add(Conversation(id="conv-1", user_id="u1", title="Test convo"))
        await db.commit()
        await PlanRepo.create(
            db, execution_id=execution_id, conversation_id="conv-1", user_id="u1",
            goal="Build a calculator", success_criteria=["add(2,3)==5"],
            steps=[{"index": 0, "title": "Step", "description": "d"}],
        )

    async def test_a_new_row_has_no_verdict_until_the_check_runs(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_plan(db)
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertIsNone(plan.goal_verdict)
            self.assertIsNone(plan.goal_verification_message)
            self.assertIsNone(plan.goal_criteria_results)

    async def test_success_verdict_is_persisted(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_plan(db)
            await PlanRepo.update_goal_verification(
                db, execution_id="e1", goal_verdict="success",
                goal_verification_message=None,
                goal_criteria_results=[{"criterion": "add(2,3)==5", "met": True}],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal_verdict, "success")
            self.assertIsNone(plan.goal_verification_message)
            self.assertEqual(plan.goal_criteria_results, [{"criterion": "add(2,3)==5", "met": True}])

    async def test_not_successful_verdict_keeps_its_message(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_plan(db)
            await PlanRepo.update_goal_verification(
                db, execution_id="e1", goal_verdict="not_successful",
                goal_verification_message="multiply was never implemented.",
                goal_criteria_results=[{"criterion": "add(2,3)==5", "met": True},
                                        {"criterion": "multiply(4,5)==20", "met": False}],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal_verdict, "not_successful")
            self.assertEqual(plan.goal_verification_message, "multiply was never implemented.")
            self.assertEqual(plan.goal_criteria_results[1]["met"], False)

    async def test_pending_criteria_results_preserve_null_met_values(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_plan(db)
            await PlanRepo.update_goal_verification(
                db, execution_id="e1", goal_verdict="pending",
                goal_verification_message="please confirm the layout yourself.",
                goal_criteria_results=[{"criterion": "looks professional", "met": None}],
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal_verdict, "pending")
            self.assertIsNone(plan.goal_criteria_results[0]["met"])

    async def test_updating_verification_does_not_touch_steps_or_goal(self):
        from core.db.repository import PlanRepo
        async with self.Session() as db:
            await self._seed_plan(db)
            await PlanRepo.update_goal_verification(
                db, execution_id="e1", goal_verdict="success",
            )
            plan = await PlanRepo.get_by_execution(db, "e1")
            self.assertEqual(plan.goal, "Build a calculator")
            self.assertEqual(plan.success_criteria, ["add(2,3)==5"])
            self.assertEqual(plan.steps[0]["title"], "Step")


if __name__ == "__main__":
    unittest.main()
