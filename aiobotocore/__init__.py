# NOTE: These imports are deprecated and will be removed in 2.x
import os

# Enabling this will enable the old http exception behavior that exposed raw aiohttp
# exceptions and old session variables available via __init__.  Disabling will swap to
# botocore exceptions and will not have these imports to match botocore.
# NOTE: without setting this to 0, retries may not work, see #876
DEPRECATED_1_4_0_APIS = int(os.getenv('AIOBOTOCORE_DEPRECATED_1_4_0_APIS', '1'))

if DEPRECATED_1_4_0_APIS:
    from .session import get_session, AioSession

    __all__ = ['get_session', 'AioSession']

__version__ = '1.4.2'
