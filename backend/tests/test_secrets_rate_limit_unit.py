# tests/test_secrets_rate_limit_unit.py
"""
Unit tests for B2-1.6 (2026-08-18): rate limiting on POST /secrets/{provider}.

This endpoint is
authenticated (any valid JWT can call it) but triggers a live third-party
provider-validation API call on every request -- previously had no rate
limiting at all, unlike the auth endpoints. Keyed per-user (not per-IP),
since a valid token is already required.

Follows the same pattern test_features.py already uses for
_signup_rate_limiter/_refresh_rate_limiter: swap the module-level limiter
for a fresh instance (test isolation), then call the endpoint function
directly rather than through a real HTTP client.

Run with:
    cd <project_root>
    python tests/test_secrets_rate_limit_unit.py
    (or: pytest tests/test_secrets_rate_limit_unit.py -v)
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSecretsEndpointRateLimit(unittest.IsolatedAsyncioTestCase):

    async def test_upsert_secret_rate_limited_per_user(self):
        import api.secrets as secrets_mod
        from config.settings import SECRETS_RATE_LIMIT_MAX_CALLS

        # Fresh limiter instance -- avoids cross-test contamination from
        # calls other tests (or an earlier run of this same test) made
        # against the shared module-level instance.
        secrets_mod._secrets_rate_limiter = type(secrets_mod._secrets_rate_limiter)(
            window_seconds=60, max_calls=SECRETS_RATE_LIMIT_MAX_CALLS,
        )

        auth = {"user_id": "rate-limit-test-user"}
        body = secrets_mod.SecretWriteRequest(api_key="sk-test-key-value")

        with patch.object(secrets_mod, "_write_secret", new=MagicMock(return_value="GROQ_API_KEY")), \
             patch.object(secrets_mod, "_validate_key", new=AsyncMock(return_value=None)):
            for _ in range(SECRETS_RATE_LIMIT_MAX_CALLS):
                result = await secrets_mod.upsert_provider_secret(
                    provider="anthropic", body=body, auth=auth
                )
                self.assertTrue(result.configured)

            with self.assertRaises(Exception) as ctx:
                await secrets_mod.upsert_provider_secret(
                    provider="anthropic", body=body, auth=auth
                )
            self.assertEqual(ctx.exception.status_code, 429)

    async def test_rate_limit_is_per_user_not_global(self):
        """A second, distinct user must not be blocked by the first user's
        exhausted limit -- confirms the key is auth["user_id"], not a
        shared/global counter."""
        import api.secrets as secrets_mod
        from config.settings import SECRETS_RATE_LIMIT_MAX_CALLS

        secrets_mod._secrets_rate_limiter = type(secrets_mod._secrets_rate_limiter)(
            window_seconds=60, max_calls=SECRETS_RATE_LIMIT_MAX_CALLS,
        )

        body = secrets_mod.SecretWriteRequest(api_key="sk-test-key-value")

        with patch.object(secrets_mod, "_write_secret", new=MagicMock(return_value="GROQ_API_KEY")), \
             patch.object(secrets_mod, "_validate_key", new=AsyncMock(return_value=None)):
            for _ in range(SECRETS_RATE_LIMIT_MAX_CALLS):
                await secrets_mod.upsert_provider_secret(
                    provider="anthropic", body=body, auth={"user_id": "user-a"}
                )
            with self.assertRaises(Exception):
                await secrets_mod.upsert_provider_secret(
                    provider="anthropic", body=body, auth={"user_id": "user-a"}
                )

            # user-b's very first call should succeed -- not affected by
            # user-a's exhausted limit.
            result = await secrets_mod.upsert_provider_secret(
                provider="anthropic", body=body, auth={"user_id": "user-b"}
            )
            self.assertTrue(result.configured)

    async def test_rejected_provider_still_counts_toward_the_limit(self):
        """The rate-limit check runs before provider validation -- even a
        request for an unsupported/invalid provider consumes a slot, so
        this can't be used to bypass the limit by intentionally failing
        early."""
        import api.secrets as secrets_mod
        from config.settings import SECRETS_RATE_LIMIT_MAX_CALLS

        secrets_mod._secrets_rate_limiter = type(secrets_mod._secrets_rate_limiter)(
            window_seconds=60, max_calls=2,
        )

        body = secrets_mod.SecretWriteRequest(api_key="sk-test-key-value")

        for _ in range(2):
            with self.assertRaises(Exception) as ctx:
                await secrets_mod.upsert_provider_secret(
                    provider="not-a-real-provider", body=body, auth={"user_id": "u"}
                )
            self.assertEqual(ctx.exception.status_code, 400)  # unknown provider, not 429 yet

        # Third call: limiter itself now fires before the unknown-provider check.
        with self.assertRaises(Exception) as ctx:
            await secrets_mod.upsert_provider_secret(
                provider="not-a-real-provider", body=body, auth={"user_id": "u"}
            )
        self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
