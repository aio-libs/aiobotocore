from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, TypeVar

import anyio

T = TypeVar('T')


def tee(itr: AsyncIterable[T], n: int = 2) -> tuple[AsyncIterator[T], ...]:
    """Backend-agnostic equivalent of ``aioitertools.tee``.

    ``aioitertools.tee`` fans values out over ``asyncio.Queue`` and
    ``asyncio.gather``, so it only runs on asyncio. This buffers per consumer
    instead, taking the lock only to pull from the source, so it runs on any
    anyio backend.
    """
    assert n > 0
    iterator = itr.__aiter__()
    buffers = [deque() for _ in range(n)]
    lock = anyio.Lock()
    # Empty until the source is done: holds None on exhaustion, else the
    # exception it raised. Replayed to every consumer, as aioitertools does.
    outcome: list[Any] = []

    async def gen(buf: deque) -> AsyncIterator[T]:
        while True:
            if buf:
                yield buf.popleft()
                continue

            async with lock:
                # Another consumer may have filled the buffer while we waited.
                if not buf and not outcome:
                    try:
                        value = await iterator.__anext__()
                    except StopAsyncIteration:
                        outcome.append(None)
                    except Exception as e:
                        outcome.append(e)
                    else:
                        for other in buffers:
                            other.append(value)

            if buf:
                continue
            if outcome[0] is not None:
                raise outcome[0]
            return

    return tuple(gen(buf) for buf in buffers)
