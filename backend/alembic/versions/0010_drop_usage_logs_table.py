"""DB-08 -- drop usage_logs, fully redundant with execution_metrics

usage_logs (one row per assistant message: prompt/completion/reasoning/total
tokens, provider, model) was written from exactly one call site
(MessageLogger.save_final_message -> DBRepo.add_usage), using the identical
`usage` dict that the same finalizer already writes into
execution_metrics.prompt_tokens/completion_tokens/reasoning_tokens. Since a
conversation's finalizer fires once per execution, the two tables recorded
the same numbers at the same grain -- a straight duplicate, not an
aggregate-vs-event relationship.

Confirmed write-only: repo-wide search found no `select(UsageLog)` and no
`.usage` relationship access anywhere outside models.py/repository.py
themselves. Every real reporting path (GET /metrics/*, GET
/projects/{id}/metrics, GET /providers/costs) already reads exclusively
from execution_metrics. The frontend's per-message token footer
(MessageItem.tsx) is fed live from the SSE 'usage' event as a message
streams in, not from any DB read -- history reload (apiMessageToUI) never
attaches usage to a historical message at all, so usage_logs was never the
source for that either.

No data loss of anything ever read: the table's own rows carried no
consumer.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usage_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usage_logs_message_id'))
    op.drop_table('usage_logs')


def downgrade() -> None:
    op.create_table(
        'usage_logs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('reasoning_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('usage_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_logs_message_id'), ['message_id'], unique=False)
