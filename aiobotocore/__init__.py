__version__ = '0.0.1'

from .session import get_session, AioSession

(get_session, AioSession)  # make pyflakes happy
