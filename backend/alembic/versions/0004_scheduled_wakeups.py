"""P-19 Phase 1 -- scheduled_wakeups table

Adds the persisted "resume me later" record backing the schedule_wakeup
tool: an execution pauses itself instead of blocking a live coroutine, and
core/execution/wakeup_scheduler.py's poll loop resolves the row later
(either wake_at elapsing or watch_execution_id reaching a terminal state)
by starting a brand-new, continuing execution in the same conversation.

A new table -- no data migration needed, no backfill.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scheduled_wakeups',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('execution_id', sa.String(), nullable=False),
        sa.Column('conversation_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('wake_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('watch_execution_id', sa.String(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resumed_execution_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scheduled_wakeups', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_execution_id'), ['execution_id'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_conversation_id'), ['conversation_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_wake_at'), ['wake_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_watch_execution_id'), ['watch_execution_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scheduled_wakeups_status'), ['status'], unique=False
        )


def downgrade() -> None:
    op.drop_table('scheduled_wakeups')
