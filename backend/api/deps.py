# api/deps.py
#
# Shared FastAPI dependency functions.
#
# Previously get_db() was copy-pasted identically in api/auth.py and
# api/chat.py (audit finding 4.2).  It lives here as the single source
# of truth; both modules import from here.

from core.db.session import AsyncSessionLocal


async def get_db():
    """Yield an async SQLAlchemy session, closing it when the request ends."""
    async with AsyncSessionLocal() as db:
        yield db
