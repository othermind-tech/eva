"""A-21 -- add user_preferences.pev_lookahead_skip_enabled

Per-user, frontend-toggleable preference (not a deployer-only env var --
requested explicitly so it can be exposed as an enable/disable control in
the session settings UI, the same way require_plan_approval already is).

When True (the default -- opt-out, not opt-in, unlike most recent boolean
toggles here), the PEV per-step verifier also checks whether a step's
evidence already satisfies one or more of the immediately-following steps,
and the runner skips invoking the agent for them rather than running them
and then verifying the no-op. Defaults True because the feature's own
guardrails (never skips the plan's final step, a hard PEV_LOOKAHEAD_MAX_SKIP
ceiling regardless of what the model reports, fail-closed on any uncertain
verification) already bound the downside; a user who wants EVA's older,
fully-literal step-by-step behavior can turn it off.


Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'pev_lookahead_skip_enabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('1'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.drop_column('pev_lookahead_skip_enabled')
