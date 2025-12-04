import asyncio
import sys
from collections.abc import Callable
from typing import TypeVar

from botocore.loaders import Loader

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

T = TypeVar("T")
P = ParamSpec("P")


async def instance_cached(
    bound_method: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    loader: Loader = bound_method.__self__

    # duplicate key building from `botocore.loaders.instance_cache`
    key = (bound_method.__name__,) + args
    for pair in sorted(kwargs.items()):
        key += pair

    # check loader instance cache
    if key in loader._cache:
        return loader._cache[key]

    # run potentially blocking method in thread
    return await asyncio.to_thread(bound_method, *args, **kwargs)
