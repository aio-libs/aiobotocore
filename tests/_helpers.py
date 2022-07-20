try:
    from contextlib import AsyncExitStack  # noqa: F401 lgtm[py/unused-import]
except ImportError:
    from async_exit_stack import (  # noqa: F401 lgtm[py/unused-import]
        AsyncExitStack,
    )
