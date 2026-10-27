from functools import lru_cache

try:
    import tiktoken
except ImportError:
    tiktoken = None


class TokenCounter:
    @staticmethod
    @lru_cache(maxsize=128)
    def _get_encoder(model_name: str):
        if not tiktoken:
            return None
        try:
            return tiktoken.encoding_for_model(model_name)
        except Exception:
            return tiktoken.get_encoding("cl100k_base")

    @classmethod
    def estimate(cls, text: str, model_name: str) -> int:
        if not text:
            return 0

        encoder = cls._get_encoder(model_name)
        if encoder:
            return len(encoder.encode(text))

        # Heuristic fallback
        return max(1, len(text) // 4)
