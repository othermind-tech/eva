"""Background process lifecycle -- add background_processes.keep_alive_on_app_shutdown

Phase 2 of session-scoped background process lifecycle (2026-09-29): the
actual persistence opt-in, built on Phase 1's process-identity fix (0017).

Default False -- session-scoped, matching the agreed policy: background
processes stop when EVA quits. True is the opt-in exception (settable by
the agent at start time, or pinned afterward by the user from the Activity
panel) for a process that should deliberately survive an app quit.


Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('background_processes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'keep_alive_on_app_shutdown',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('background_processes', schema=None) as batch_op:
        batch_op.drop_column('keep_alive_on_app_shutdown')
