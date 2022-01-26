from contextlib import contextmanager
from types import ModuleType
from typing import Union

import wrapt


# wrapper must look like this
# def _wrapt_cancel_method(wrapped, instance, args, kwargs):
#     return wrapped(*args, **kwargs)

@contextmanager
def wrapt_function(module: Union[str, ModuleType], name: str, wrapped_method):
    (parent, attribute, original) = wrapt.resolve_path(module, name)
    wrapt.wrap_function_wrapper(module, name, wrapped_method)

    try:
        yield
    finally:
        if not isinstance(getattr(parent, attribute), wrapt.ObjectProxy):
            return

        setattr(parent, attribute, original)
