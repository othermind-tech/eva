"""Daemon kind -- daemon_configs.kind column

Adds DaemonConfig.kind ("standard" | "system" | "supervisor"), replacing
the previous name-string comparison (daemon.name == sync_daemon_name(project_id))
that special-cased the project-sync-daemon with a real semantic type. See
core/db/models.py's DaemonConfig docstring and core/daemons/runner.py's
kind-based check.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('daemon_configs') as batch_op:
        batch_op.add_column(
            sa.Column('kind', sa.String(), server_default='standard', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('daemon_configs') as batch_op:
        batch_op.drop_column('kind')
