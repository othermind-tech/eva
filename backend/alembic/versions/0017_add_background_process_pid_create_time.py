"""Background process lifecycle -- add background_processes.pid_create_time

Phase 1 of session-scoped background process lifecycle (2026-09-29): a
process-identity fix, independent of and prerequisite to the persistence
policy work that follows it.

A bare PID lookup (_pid_alive) can't tell "the process we started" apart
from an unrelated process the OS has since reused the same PID for --
realistic after a long backend/machine uptime or a reboot. Every consumer
of a BackgroundProcess row that decides whether it's still alive (boot
reconciliation, stop_background, the daily max-lifetime reaper, and
start-action duplicate-detection) previously trusted PID equality alone.

pid_create_time (via psutil.Process(pid).create_time(), captured once at
spawn time in ShellExecutor.start_background()) lets those call sites
verify identity, not just existence. Nullable: existing rows created
before this column existed have no captured value and fall back to the
old, identity-blind liveness check (see core/shell/executor.py's
_pid_matches_process()) -- no behaviour change for them, only for rows
created going forward.


Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('background_processes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('pid_create_time', sa.Float(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('background_processes', schema=None) as batch_op:
        batch_op.drop_column('pid_create_time')
