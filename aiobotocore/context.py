from functools import wraps

from botocore import context


def with_current_context(hook=None):
    """
    Decorator that wraps ``start_as_current_context`` and optionally invokes a
    hook within the newly-set context. This is just syntactic sugar to avoid
    indenting existing code under the context manager.
    Example usage:
        @with_current_context(partial(register_feature_id, 'MY_FEATURE'))
        async def my_feature():
            pass
    :type hook: callable
    :param hook: A callable that will be invoked within the scope of the
        ``start_as_current_context`` context manager.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            with context.start_as_current_context():
                if hook:
                    hook()
                return await func(*args, **kwargs)

        return wrapper

    return decorator
