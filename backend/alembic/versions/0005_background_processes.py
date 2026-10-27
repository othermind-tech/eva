"""Background process registry -- background_processes table

Adds the PID/process registry backing the background_process tool: a
tracked, detached OS process (e.g. a dev server) started outside
ShellExecutor's normal blocking/timeout-bound run() path. DB-backed (not an
in-memory registry like ExecutionRegistry) so a process that legitimately
survives a backend restart isn't lost -- see main.py's boot-time
reconciliation sweep, which re-adopts still-alive rows on startup.

A new table -- no data migration needed, no backfill.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'background_processes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('conversation_id', sa.String(), nullable=True),
        sa.Column('project_id', sa.String(), nullable=True),
        sa.Column('execution_id', sa.String(), nullable=True),
        sa.Column('command', sa.JSON(), nullable=False),
        sa.Column('cwd', sa.String(), nullable=False),
        sa.Column('log_path', sa.String(), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='running'),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('last_output_offset', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('background_processes', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_background_processes_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_background_processes_conversation_id'), ['conversation_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_background_processes_project_id'), ['project_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_background_processes_execution_id'), ['execution_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_background_processes_status'), ['status'], unique=False
        )


def downgrade() -> None:
    op.drop_table('background_processes')
