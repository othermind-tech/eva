"""A-20 -- add execution_plans.success_criteria

The plan verifier (core/execution/plan_verifier.py) judges a finished plan
against success_criteria (1-4 checkable statements the planner now
generates -- prompts/planner.py), but that field only ever lived on the
in-memory plan dict for the duration of one run. Persisting it means a past
run's criteria can actually be inspected later (debugging, a "why did this
end up pending" UI) instead of being gone the moment the process moved on.

Nullable, no backfill: existing rows predate this field and have no real
criteria to backfill from (the goal text itself is the only close analogue,
and synthesizing one after the fact would misrepresent what the plan
actually stated at the time).


Revision ID: 0014
Revises: 0013
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('execution_plans', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('success_criteria', sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('execution_plans', schema=None) as batch_op:
        batch_op.drop_column('success_criteria')
