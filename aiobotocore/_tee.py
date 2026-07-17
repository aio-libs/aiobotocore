from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, Generic, TypeVar

T = TypeVar('T')


class _TeeState(Generic[T]):
    """Shared source and per-consumer buffers behind a set of tee iterators."""

    def __init__(self, itr: AsyncIterable[T], n: int) -> None:
        import anyio

        self.iterator = itr.__aiter__()
        self.buffers = [deque() for _ in range(n)]
        self.lock = anyio.Lock()
        self.cancelled_exc_class = anyio.get_cancelled_exc_class()
        # Empty until the source is done: holds None on exhaustion, else the
        # exception it raised. Replayed to every consumer, as aioitertools does.
        self.outcome: list[Any] = []

    async def pull(self, buf: deque) -> None:
        """Advance the source once, fanning the value out to every buffer.

        Only the caller whose buffer is still empty needs a value; take the
        lock, then bail if another consumer filled it (or finished the source)
        while we waited.
        """
        async with self.lock:
            if buf or self.outcome:
                return
            try:
                value = await self.iterator.__anext__()
            except StopAsyncIteration:
                self.outcome.append(None)
            except self.cancelled_exc_class:
                # Cancelling this consumer tears down the source, which is
                # shared. Make the others fail loudly rather than silently
                # yield a truncated stream.
                self.outcome.append(RuntimeError('tee source was cancelled'))
                raise
            except Exception as e:
                self.outcome.append(e)
            else:
                for other in self.buffers:
                    other.append(value)


class _TeeIterator(AsyncIterator[T]):
    def __init__(self, state: _TeeState[T], buf: deque) -> None:
        self._state = state
        self._buf = buf
        self._done = False

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def __anext__(self) -> T:
        if self._done:
            raise StopAsyncIteration

        buf = self._buf
        state = self._state
        while not buf:
            await state.pull(buf)
            if buf:
                break
            # Source is done: replay its outcome, then stay stopped.
            self._done = True
            if state.outcome[0] is not None:
                raise state.outcome[0]
            raise StopAsyncIteration
        return buf.popleft()


def tee(itr: AsyncIterable[T], n: int = 2) -> tuple[AsyncIterator[T], ...]:
    """Backend-agnostic equivalent of ``aioitertools.tee``.

    ``aioitertools.tee`` fans values out over ``asyncio.Queue`` and
    ``asyncio.gather``, so it only runs on asyncio. This buffers per consumer
    instead, taking the lock only to pull from the source, so it runs on any
    anyio backend.
    """
    if n <= 0:
        raise ValueError('n must be >= 1')

    state: _TeeState[T] = _TeeState(itr, n)
    return tuple(_TeeIterator(state, buf) for buf in state.buffers)
