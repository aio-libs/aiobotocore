import inspect
from asyncio import AbstractEventLoop
from concurrent.futures import Executor
from typing import Callable, Optional


async def resolve_awaitable(obj):
    if inspect.isawaitable(obj):
        return await obj

    return obj


async def async_any(items):
    for item in items:
        if await resolve_awaitable(item):
            return True

    return False


async def optionally_run_in_executor(
    loop: AbstractEventLoop,
    executor: Optional[Executor],
    func: Callable,
    *args,
):
    if executor:
        return await loop.run_in_executor(executor, func, *args)

    return func(*args)
