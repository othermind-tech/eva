"""A-20 -- add execution_plans goal-verification columns

PlanVerifierEngine's tri-state verdict (success/pending/not_successful),
its per-criterion criteria_results, and its user-facing message only ever
existed as an SSE-only StepProgressChunk, held in the in-memory
LocalShadowBuffer for a limited window post-completion, then gone --
confirmed empirically: querying
GET /executions/{id}/trace shortly after a run completed already showed the
step_progress data evicted, silently falling back to a DB-only
reconstruction that never had it to begin with. The verdict's *effect* (its
message, appended to the saved answer) survives in `messages`; the
structured data itself did not survive anywhere.

Three new nullable columns, written once per run, right after
PlanVerifierEngine.verify_plan() returns (core/execution/pev_runner.py).
Nullable, no backfill -- existing rows predate this check entirely, not
"ran and produced no result".


Revision ID: 0015
Revises: 0014
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('execution_plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('goal_verdict', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('goal_verification_message', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('goal_criteria_results', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('execution_plans', schema=None) as batch_op:
        batch_op.drop_column('goal_criteria_results')
        batch_op.drop_column('goal_verification_message')
        batch_op.drop_column('goal_verdict')
