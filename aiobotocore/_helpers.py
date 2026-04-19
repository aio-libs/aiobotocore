"""Helper utilities for async operations in aiobotocore."""


async def resolve_awaitable(obj):
    """Resolve an object that may or may not be awaitable.

    Args:
        obj: An object that may be awaitable (coroutine, Task, Future) or
             a non-awaitable value.

    Returns:
        The result of awaiting the object if it was awaitable,
        otherwise the object itself.
    """
    if hasattr(obj, '__await__'):
        return await obj
    return obj


async def async_any(items):
    """Return True if any item in the iterable is truthy (after awaiting).

    Unlike the built-in `any()`, this properly handles awaitable values.

    Args:
        items: An iterable of items, which may be awaitable.

    Returns:
        True if any item evaluates to truthy, False otherwise.
    """
    for item in items:
        if await resolve_awaitable(item):
            return True
    return False
