"""B1-2.1 / B1-2.2 -- drop two never-enforced/never-populated columns

  - users.is_active           -- defined but never enforced anywhere in
    application code. Dated from an earlier web-hosted/multi-tenant design
    phase. If EVA moves toward a hosted multi-user mode again (strategic
    report Pillar 3), reinstate a real is_active column with actual
    enforcement then -- do not just re-add this one unused.
  - auth_audit_logs.event_metadata -- defined and migrated in
    0001_initial_schema.py but never populated by any of the 3 call sites
    (signup, login, logout) that write AuthAuditLog rows.


Revision ID: 0008
Revises: 0007
Create Date: 2026-08-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_active')
    with op.batch_alter_table('auth_audit_logs', schema=None) as batch_op:
        batch_op.drop_column('event_metadata')


def downgrade() -> None:
    with op.batch_alter_table('auth_audit_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_metadata', sa.Text(), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=True, server_default=sa.true()))
