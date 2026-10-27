# core/utils.py
import anyio
from typing import Generator, AsyncGenerator, TypeVar

T = TypeVar("T")


async def iterate_sync_generator(sync_gen: Generator[T, None, None]) -> AsyncGenerator[T, None]:
    """
    Convert a synchronous generator (blocking, yields items over time)
    into an async generator that yields items as they become available.

    It uses anyio.to_thread.run_sync to call next(sync_gen) without blocking the event loop.
    """
    try:
        while True:
            try:
                # call next() in a thread so we don't block the event loop
                item = await anyio.to_thread.run_sync(lambda: next(sync_gen))
            except StopIteration:
                break
            yield item
    finally:
        # ensure generator close() is called if present
        try:
            await anyio.to_thread.run_sync(lambda: sync_gen.close())
        except Exception:
            # ignore close errors
            pass
