from __future__ import annotations

from enum import Enum

from .httpxsession import is_httpx_session_cls


class AsyncPrimitives(Enum):
    ASYNCIO = 'asyncio'
    ANYIO = 'anyio'


def infer_async_primitives(http_session_cls) -> AsyncPrimitives:
    if is_httpx_session_cls(http_session_cls):
        return AsyncPrimitives.ANYIO
    return AsyncPrimitives.ASYNCIO
