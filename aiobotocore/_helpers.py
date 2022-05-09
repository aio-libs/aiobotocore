import inspect

try:
    from contextlib import asynccontextmanager  # noqa: F401 lgtm[py/unused-import]
except ImportError:
    from async_generator import asynccontextmanager  # noqa: F401 E501, lgtm[py/unused-import]


async def resolve_awaitable(obj):
    if inspect.isawaitable(obj):
        return await obj

    return obj


async def async_any(items):
    for item in items:
        if await resolve_awaitable(item):
            return True

    return False
