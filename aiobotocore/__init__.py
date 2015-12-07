__version__ = '0.0.2'

from .session import get_session, AioSession

(get_session, AioSession)  # make pyflakes happy
