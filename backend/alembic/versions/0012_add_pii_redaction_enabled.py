"""S-03 -- add user_preferences.pii_redaction_enabled

New opt-in per-user toggle: when True, the user's own chat message is
scrubbed of PII (email, phone, SSN, credit card, etc. -- see
core/security/pii_redactor.py's DEFAULT_PII_ENTITIES) before it is
persisted or sent to the LLM. Off by default -- matches S-03's existing
opt-in scope. Tool-output redaction is separate and already opt-in via a
"redact_pii" entry in tool_hooks (no schema change needed there -- it
reuses the existing tool_hooks JSON column).


Revision ID: 0012
Revises: 0011
Create Date: 2026-09-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'pii_redaction_enabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.drop_column('pii_redaction_enabled')
