"""B2-3.2 / B2-3.3 -- indexes on columns the daily auth-retention purge filters on

Adds indexes to three existing columns, no new columns and no data
migration needed:
  - auth_audit_logs.created_at    -- filtered by purge_old_audit_logs()
    on every single daily sweep (DB-01, core/db/repository.py).
  - auth_sessions.revoked         -- filtered by purge_expired_sessions()
    on every daily sweep.
  - auth_sessions.refresh_expires_at -- also filtered by
    purge_expired_sessions().

Without these, the daily cleanup pass (core/tasks/cleanup.py) does an
increasingly full table scan on each of these tables as they grow between
purges.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-18

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('auth_audit_logs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_auth_audit_logs_created_at'), ['created_at'], unique=False
        )
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_auth_sessions_revoked'), ['revoked'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_auth_sessions_refresh_expires_at'), ['refresh_expires_at'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_sessions_refresh_expires_at'))
        batch_op.drop_index(batch_op.f('ix_auth_sessions_revoked'))
    with op.batch_alter_table('auth_audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_audit_logs_created_at'))
