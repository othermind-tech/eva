"""V6.8 -- user_preferences.max_step_extensions

Adds a per-user setting controlling the maximum number of extra step
extensions the agent may request during Plan->Execute->Verify execution
before being forced to conclude. Defaults to 2, mirroring the in-code
default in config/settings.py's DEFAULT_SESSION.

Existing rows get the default via server_default so no backfill is
required. SQLite ALTER TABLE ADD COLUMN with a server_default is applied
directly (no batch mode needed for a simple additive column, but we use
batch mode for consistency with the rest of this migration chain and to
stay safe under SQLite's ALTER TABLE limitations).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.add_column(
            sa.Column('max_step_extensions', sa.Integer(), server_default='2', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.drop_column('max_step_extensions')
