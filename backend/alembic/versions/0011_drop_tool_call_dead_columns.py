"""DB-10 phase 1 -- drop confirmed-dead tool_calls/messages columns

Three columns confirmed write-only, never read anywhere:
  - tool_calls.result   -- always written None from the only reachable call
    site (core/execution/worker.py's _save_tool_step). The other call site
    that could have passed a real value (core/logging/message_logger.py's
    save_final_message, behind a `tool_call` param) was itself unreachable
    -- its sole caller always passed tool_call=None -- and turned out to
    also be broken (a 5-positional-arg call against add_tool_call's
    6-parameter signature, an immediate TypeError had it ever fired). That
    whole dead branch and the unused `tool_call` param are removed here too.
  - tool_calls.call_id / messages.tool_call_id -- written on every tool
    step (twice, once per table) but never read: the one method that could
    read the linkage, DBRepo.get_tool_calls_for_message(), is never called
    anywhere. Confirmed by the V6.14.2 postmortem's own grep at the time
    ("zero -- written but never read anywhere") and reconfirmed now.

Deliberately NOT touched: tool_calls.tool_name/arguments. Confirmed live
consumer -- core/memory/engine.py's MemoryEngine.build_context(), wired
into the live ReAct loop (core/agent_engine.py, core/execution/worker.py)
on every conversation turn. For a step with multiple simultaneous tool
calls, messages.tool_args/tool_name are None (one arg-set per message row,
a known, deliberately-deferred limitation) -- tool_calls is the only place
those survive in that case, so this is not the write-only pattern DB-08
was. Consolidating tool_name/arguments onto one source of truth (and
properly fixing multi-call representation) is a separate, larger decision
-- not part of this migration.


Revision ID: 0011
Revises: 0010
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tool_calls', schema=None) as batch_op:
        batch_op.drop_column('call_id')
        batch_op.drop_column('result')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('tool_call_id')


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tool_call_id', sa.String(), nullable=True))
    with op.batch_alter_table('tool_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('result', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('call_id', sa.String(), nullable=True))
