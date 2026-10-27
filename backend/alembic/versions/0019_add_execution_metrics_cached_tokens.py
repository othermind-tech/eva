"""Add execution_metrics.cached_tokens

V6.59 Phase 6: prompt tokens served from the provider's prompt cache. Several
providers report it (OpenAI-compatible `prompt_tokens_details.cached_tokens`,
Anthropic `cache_read_input_tokens`; e.g. 68 of a 69-token prompt on Ollama
Cloud) but EVA never stored it, so cache savings were invisible and cost was
priced as if every prompt token were uncached.

`prompt_tokens` stays the TOTAL input; `cached_tokens` is the subset served from
cache (LiteLLM's cost_per_token takes the same convention). Existing rows get 0
(unknown/none) -- history is not rewritten.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('execution_metrics', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'cached_tokens',
                sa.Integer(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('execution_metrics', schema=None) as batch_op:
        batch_op.drop_column('cached_tokens')
