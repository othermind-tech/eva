"""DB-09 -- drop dead sessions table, repoint conversations.session_id to auth_sessions

SessionModel/"sessions" was confirmed dead: no live code path anywhere ever
writes or reads it (DBRepo.create_session/get_session/touch_session existed
but were never called outside their own definitions). conversations.session_id
has always actually been populated with AuthSession.id values (see
api/chat.py -> core/auth/dependencies.py, which resolves it from the JWT
"sid" claim) via a FK that pointed at the unused sessions table instead --
this repoints it to what it really references and drops the dead table.

No data loss: conversations.session_id's own values are untouched (only its
FK *constraint* changes, not the column or its data); sessions has no data
any live path ever wrote into it.


Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    # The original FK (0001_initial_schema.py) was created unnamed, per
    # SQLite/SQLAlchemy convention -- reflection confirms constraint
    # name=None. naming_convention lets batch mode compute a deterministic
    # name to match it against for the drop, without needing the DB's own
    # (nonexistent) constraint name.
    with op.batch_alter_table(
        'conversations', schema=None, naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint('fk_conversations_session_id_sessions', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_conversations_session_id_auth_sessions',
            'auth_sessions', ['session_id'], ['id'],
        )
    op.drop_table('sessions')


def downgrade() -> None:
    op.create_table(
        'sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_constraint('fk_conversations_session_id_auth_sessions', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_conversations_session_id_sessions',
            'sessions', ['session_id'], ['id'],
        )
