"""An async reimplementation of the blocking elements from botocore.retries.adaptive."""

import asyncio
import logging
from typing import Any

from botocore.retries import standard, throttling

# The RateClocker from botocore uses a threading.Lock, but in a single-threaded asyncio
# program, the lock will be acquired then released by the same coroutine without
# blocking.
from botocore.retries.adaptive import RateClocker

from . import bucket

logger = logging.getLogger(__name__)


def register_retry_handler(client):
    """Register the async adaptive retry rate limiter with a client.

    Args:
        client: The botocore client to register the rate limiter with.

    Returns:
        The AsyncClientRateLimiter instance.
    """
    clock = bucket.Clock()
    rate_adjustor = throttling.CubicCalculator(
        starting_max_rate=0, start_time=clock.current_time()
    )
    token_bucket = bucket.AsyncTokenBucket(max_rate=1, clock=clock)
    rate_clocker = RateClocker(clock)
    throttling_detector = standard.ThrottlingErrorDetector(
        retry_event_adapter=standard.RetryEventAdapter(),
    )
    limiter = AsyncClientRateLimiter(
        rate_adjustor=rate_adjustor,
        rate_clocker=rate_clocker,
        token_bucket=token_bucket,
        throttling_detector=throttling_detector,
        clock=clock,
    )
    client.meta.events.register(
        'before-send',
        limiter.on_sending_request,
    )
    client.meta.events.register(
        'needs-retry',
        limiter.on_receiving_response,
    )
    return limiter


class AsyncClientRateLimiter:
    """An async reimplementation of ClientRateLimiter.

    This rate limiter implements adaptive retry behavior for AWS requests,
    automatically adjusting request rates based on throttling responses.

    The limiter starts in a disabled state and only activates after the first
    throttling error is detected. Once enabled, it controls request rate using
    a token bucket algorithm.

    Most of the code here comes directly from botocore. The main change is making the
    callbacks async.
    This doesn't inherit from the botocore ClientRateLimiter for two reasons:
    * the interface is slightly changed (methods are now async)
    * we rewrote the entirety of the class anyway
    """

    _MAX_RATE_ADJUST_SCALE = 2.0

    def __init__(
        self,
        rate_adjustor,
        rate_clocker,
        token_bucket,
        throttling_detector,
        clock,
    ):
        self._rate_adjustor = rate_adjustor
        self._rate_clocker = rate_clocker
        self._token_bucket = token_bucket
        self._throttling_detector = throttling_detector
        self._clock = clock
        self._enabled = False
        self._lock = asyncio.Lock()

    async def on_sending_request(self, request: Any, **kwargs: Any) -> None:
        """Hook called before sending a request to apply rate limiting.

        Args:
            request: The request being sent.
            **kwargs: Additional arguments from the event system.
        """
        if self._enabled:
            await self._token_bucket.acquire()

    async def on_receiving_response(self, **kwargs: Any) -> None:
        """Hook called when receiving a response to adjust rate limits.

        Called by the 'needs-retry' event. Updates the rate limit based on
        whether the response was successful or indicated throttling.

        Args:
            **kwargs: Arguments from the retry event, includes response
                     information for throttling detection.
        """
        measured_rate = self._rate_clocker.record()
        timestamp = self._clock.current_time()
        async with self._lock:
            if not self._throttling_detector.is_throttling_error(**kwargs):
                new_rate = self._rate_adjustor.success_received(timestamp)
            else:
                if not self._enabled:
                    rate_to_use = measured_rate
                else:
                    rate_to_use = min(
                        measured_rate, self._token_bucket.max_rate
                    )
                new_rate = self._rate_adjustor.error_received(
                    rate_to_use, timestamp
                )
                logger.debug(
                    "Throttling response received, new send rate: %s "
                    "measured rate: %s, token bucket capacity "
                    "available: %s",
                    new_rate,
                    measured_rate,
                    self._token_bucket.available_capacity,
                )
                self._enabled = True
            # Guard against division by zero when measured_rate is very small or zero
            if measured_rate > 0:
                max_allowed_rate = self._MAX_RATE_ADJUST_SCALE * measured_rate
            else:
                max_allowed_rate = self._MAX_RATE_ADJUST_SCALE
            await self._token_bucket.set_max_rate(
                min(new_rate, max_allowed_rate)
            )
