"""S-11 -- add mcp_configs.credential_refs

New nullable column on MCPConfig: a JSON list of
{"key": ..., "credential_ref": "mcp:{config_id}:{key}"} entries marking
which keys in env/headers have their real value moved to the OS keyring
(the same mechanism api/secrets.py already uses for provider API keys)
and replaced in-place with a redaction placeholder.

Deliberately NO server_default -- NULL is a real, load-bearing sentinel
distinguishing "this row predates S-11, not yet migrated" from "already
migrated, genuinely has no secret-shaped keys" (a real, persisted []).
The startup migration pass in main.py's lifespan uses exactly this
distinction. A boolean/count column with a default would collapse that
distinction and either re-scan every row on every boot forever, or skip
rows that genuinely need migrating.


Revision ID: 0013
Revises: 0012
Create Date: 2026-09-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('mcp_configs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('credential_refs', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('mcp_configs', schema=None) as batch_op:
        batch_op.drop_column('credential_refs')
