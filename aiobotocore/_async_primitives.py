from __future__ import annotations

import asyncio
from enum import Enum

from .httpxsession import is_httpx_session_cls


class AsyncPrimitives(Enum):
    ASYNCIO = 'asyncio'
    ANYIO = 'anyio'


def infer_async_primitives(http_session_cls) -> AsyncPrimitives:
    if is_httpx_session_cls(http_session_cls):
        return AsyncPrimitives.ANYIO
    return AsyncPrimitives.ASYNCIO


def create_lock(primitives: AsyncPrimitives):
    if primitives is AsyncPrimitives.ANYIO:
        # anyio is a hard dependency of the httpx backend.
        import anyio

        return anyio.Lock()
    return asyncio.Lock()
