from contextlib import asynccontextmanager
from copy import deepcopy
from functools import wraps
from inspect import iscoroutinefunction

from botocore.context import (
    ClientContext,
    get_context,
    reset_context,
    set_context,
)


@asynccontextmanager
async def start_as_current_context(ctx=None):
    current = ctx or get_context()
    if current is None:
        new = ClientContext()
    else:
        new = deepcopy(current)
    token = set_context(new)
    try:
        yield
    finally:
        reset_context(token)


def with_current_context(hook=None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with start_as_current_context():
                if hook:
                    if iscoroutinefunction(hook):
                        await hook()
                    else:
                        hook()
                return await func(*args, **kwargs)

        return wrapper

    return decorator
