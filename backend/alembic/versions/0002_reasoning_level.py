"""V6.7 -- user_preferences.reasoning: Boolean -> String (level)

EVA's reasoning setting moves from an on/off boolean to a level string
("minimal"/"low"/"medium"/"high"/"xhigh", or NULL for off) so it can carry
provider-native depth ("reasoning effort") instead of just enable/disable.

Data preservation: existing True -> "medium" (a reasonable default depth
for users who already had reasoning on); existing False -> NULL (off).
SQLite can't change a column's type in place -- add the new column under a
temporary name, populate it from the old column while both still exist,
then drop the old column and rename the temp one into its place, all via
Alembic's batch mode (env.py already has render_as_batch=True for this).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.add_column(sa.Column('_reasoning_level_tmp', sa.String(), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE user_preferences SET _reasoning_level_tmp = 'medium' WHERE reasoning = 1"
    ))
    # reasoning = 0 (or NULL) -> _reasoning_level_tmp stays NULL (off) -- no
    # UPDATE needed, that's the column's default state.

    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.drop_column('reasoning')
        batch_op.alter_column('_reasoning_level_tmp', new_column_name='reasoning')


def downgrade() -> None:
    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.add_column(sa.Column('_reasoning_bool_tmp', sa.Boolean(), nullable=False, server_default=sa.false()))

    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE user_preferences SET _reasoning_bool_tmp = 1 "
        "WHERE reasoning IS NOT NULL AND reasoning != 'off'"
    ))

    with op.batch_alter_table('user_preferences') as batch_op:
        batch_op.drop_column('reasoning')
        batch_op.alter_column('_reasoning_bool_tmp', new_column_name='reasoning')
