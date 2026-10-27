# utils/child_env.py
#
# The environment EVA hands to child processes it starts on an agent's behalf
# (shell commands, background processes, git) -- V6.59 Phase 3.
#
# What must not reach an agent-driven child process:
#
#  * the LLM provider-key variables EVA manages (GROQ_API_KEY, OPENAI_API_KEY,
#    ...). EVA no longer exports its stored keys, but the server can still
#    inherit one from the user's own Windows/user environment, and an agent
#    shell that can run `printenv` could then read -- and a prompt-injected
#    command exfiltrate -- a live key. (The pre-Phase-3 loader deleted these
#    from the server's environment, so agent shells never saw them; this keeps
#    that guarantee without touching the server's own environment.) To give a
#    command a key deliberately, `export` it in the shell session: `extra`
#    wins over the strip.
#
# (EVA's token-signing key is not in the environment at all since V6.59 Phase 7
# -- core/auth/signing_key.py reads it from the credential store -- so there is
# nothing of EVA's own left to strip. A variable literally named SECRET_KEY is
# therefore the user's own, e.g. a Django project, and is passed through.)
#
# Strip them here, once, instead of at each spawn site.
#
# Deliberately NOT stripped: other credential-looking variables the user owns
# (GITHUB_TOKEN, AWS_ACCESS_KEY_ID, ...). Shell/git/cloud tooling legitimately
# needs them and they are not LLM provider keys EVA manages.

from __future__ import annotations

import os
from typing import Dict, Mapping, Optional

from providers import registry

#: Variables EVA itself puts in its own process environment that must never
#: reach a child process. Empty since Phase 7: EVA keeps no secret in its
#: environment. Kept as the single place to list one if that ever changes.
EVA_INTERNAL_SECRET_VARS: frozenset = frozenset()

#: LLM provider-key variable names EVA manages. AWS_ACCESS_KEY_ID is excluded:
#: it is a generic cloud-credential name whose companion secret EVA does not
#: manage, and AWS tooling run through the shell needs it.
EVA_MANAGED_KEY_VARS = frozenset(
    spec.env_key for spec in registry.PROVIDERS.values()
    if spec.env_key and spec.env_key != "AWS_ACCESS_KEY_ID"
)

_STRIPPED = frozenset(n.upper() for n in EVA_INTERNAL_SECRET_VARS | EVA_MANAGED_KEY_VARS)


def child_environment(
    base: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """
    A copy of `base` (default: os.environ) without EVA-internal secrets and
    EVA-managed provider-key variables, then
    updated with `extra` (e.g. a shell session's exported variables -- these
    are the user's/agent's own and win, even over a stripped name).
    """
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k.upper() not in _STRIPPED}
    if extra:
        env.update(extra)
    return env
