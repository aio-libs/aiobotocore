# The following tests are the adaptation of the unit tests for the original (sync)
# ClientRateLimiter and TokenBucket in botocore.
# see: https://github.com/boto/botocore:
# `/tests/unit/retries/test_bucket.py` and `/tests/unit/retries/test_adaptive.py`.

from unittest import mock

import pytest
from botocore.exceptions import CapacityNotAvailableError
from botocore.retries import standard, throttling

from aiobotocore.retries import adaptive, bucket


class _SleepMethodCalled(Exception):
    """Raised to explicitly fail a test for calling the blocking `sleep` method."""

    pass


class FakeClock(bucket.Clock):
    def __init__(self, timestamp_sequences):
        self.timestamp_sequences = timestamp_sequences
        self.sleep_call_amounts = []

    def sleep(self, amount):
        raise _SleepMethodCalled(
            "sleep method should never be called, non-blocking behavior expected"
        )

    def current_time(self):
        return self.timestamp_sequences.pop(0)


class TestAsyncClientRateLimiter:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.timestamp_sequences = [0]
        self.clock = FakeClock(self.timestamp_sequences)
        self.token_bucket = mock.Mock(spec=bucket.AsyncTokenBucket)
        self.rate_adjustor = mock.Mock(spec=throttling.CubicCalculator)
        self.rate_clocker = mock.Mock(spec=adaptive.RateClocker)
        self.throttling_detector = mock.Mock(
            spec=standard.ThrottlingErrorDetector
        )

    def create_client_limiter(self):
        rate_limiter = adaptive.AsyncClientRateLimiter(
            rate_adjustor=self.rate_adjustor,
            rate_clocker=self.rate_clocker,
            token_bucket=self.token_bucket,
            throttling_detector=self.throttling_detector,
            clock=self.clock,
        )
        return rate_limiter

    @pytest.mark.asyncio
    async def test_bucket_bucket_acquisition_only_if_enabled(self):
        rate_limiter = self.create_client_limiter()
        await rate_limiter.on_sending_request(request=mock.sentinel.request)
        assert not self.token_bucket.acquire.called

    @pytest.mark.asyncio
    async def test_token_bucket_enabled_on_throttling_error(self):
        rate_limiter = self.create_client_limiter()
        self.throttling_detector.is_throttling_error.return_value = True
        self.rate_clocker.record.return_value = 21
        self.rate_adjustor.error_received.return_value = 17
        await rate_limiter.on_receiving_response()
        # Now if we call on_receiving_response we should try to acquire
        # token.
        self.timestamp_sequences.append(1)
        await rate_limiter.on_sending_request(request=mock.sentinel.request)
        assert self.token_bucket.acquire.called

    @pytest.mark.asyncio
    async def test_max_rate_updated_on_success_response(self):
        rate_limiter = self.create_client_limiter()
        self.throttling_detector.is_throttling_error.return_value = False
        self.rate_adjustor.success_received.return_value = 20
        self.rate_clocker.record.return_value = 21
        await rate_limiter.on_receiving_response()
        assert await self.token_bucket.set_max_rate.called_with(20)

    @pytest.mark.asyncio
    async def test_max_rate_cant_exceed_20_percent_max(self):
        rate_limiter = self.create_client_limiter()
        self.throttling_detector.is_throttling_error.return_value = False
        # So if our actual measured sending rate is 20 TPS
        self.rate_clocker.record.return_value = 20
        # But the rate adjustor is telling us to go up to 100 TPS
        self.rate_adjustor.success_received.return_value = 100

        # The most we should go up is 2.0 * 20
        await rate_limiter.on_receiving_response()
        assert await self.token_bucket.set_max_rate.called_with(2.0 * 20)


class TestAsyncTokenBucket:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.timestamp_sequences = [0]
        self.clock = FakeClock(self.timestamp_sequences)

    def create_token_bucket(self, max_rate=10, min_rate=0.1):
        return bucket.AsyncTokenBucket(
            max_rate=max_rate, clock=self.clock, min_rate=min_rate
        )

    @pytest.mark.asyncio
    async def test_can_acquire_amount(self):
        self.timestamp_sequences.extend(
            [
                # Requests tokens every second, which is well below our
                # 10 TPS fill rate.
                1,
                2,
                3,
                4,
                5,
            ]
        )
        token_bucket = self.create_token_bucket(max_rate=10)
        for _ in range(5):
            assert await token_bucket.acquire(1, block=False)

    @pytest.mark.asyncio
    async def test_can_change_max_capacity_lower(self):
        # Requests at 1 TPS.
        self.timestamp_sequences.extend([1, 2, 3, 4, 5])
        token_bucket = self.create_token_bucket(max_rate=10)
        # Request the first 5 tokens with max_rate=10
        for _ in range(5):
            assert await token_bucket.acquire(1, block=False)
        # Now scale the max_rate down to 1 on the 5th second.
        self.timestamp_sequences.append(5)
        await token_bucket.set_max_rate(1)
        # And then from seconds 6-10 we request at one per second.
        self.timestamp_sequences.extend([6, 7, 8, 9, 10])
        for _ in range(5):
            assert await token_bucket.acquire(1, block=False)

    @pytest.mark.asyncio
    async def test_max_capacity_is_at_least_one(self):
        token_bucket = self.create_token_bucket()
        self.timestamp_sequences.append(1)
        await token_bucket.set_max_rate(0.5)
        assert token_bucket._fill_rate == 0.5
        assert token_bucket._max_capacity == 1

    @pytest.mark.asyncio
    async def test_acquire_fails_on_non_block_mode_returns_false(self):
        self.timestamp_sequences.extend(
            [
                # Initial creation time.
                0,
                # Requests a token 1 second later.
                1,
            ]
        )
        token_bucket = self.create_token_bucket(max_rate=10)
        with pytest.raises(CapacityNotAvailableError):
            await token_bucket.acquire(100, block=False)

    @pytest.mark.asyncio
    async def test_can_retrieve_at_max_send_rate(self):
        self.timestamp_sequences.extend(
            [
                # Request a new token every 100ms (10 TPS) for 2 seconds.
                1 + 0.1 * i
                for i in range(20)
            ]
        )
        token_bucket = self.create_token_bucket(max_rate=10)
        for _ in range(20):
            assert await token_bucket.acquire(1, block=False)

    @pytest.mark.asyncio
    async def test_acquiring_blocks_when_capacity_reached(self):
        # This is 1 token every 0.1 seconds.
        token_bucket = self.create_token_bucket(max_rate=10)
        self.timestamp_sequences.extend(
            [
                # The first acquire() happens after .1 seconds.
                0.1,
                # The second acquire() will fail because we get tokens at
                # 1 per 0.1 seconds.  We will then sleep for 0.05 seconds until we
                # get a new token.
                0.15,
                # And at 0.2 seconds we get our token.
                0.2,
                # And at 0.3 seconds we have no issues getting a token.
                # Because we're using such small units (to avoid bloating the
                # test run time), we have to go slightly over 0.3 seconds here.
                0.300001,
            ]
        )
        assert await token_bucket.acquire(1, block=False)
        assert token_bucket._current_capacity == 0
        assert await token_bucket.acquire(1, block=True)
        assert token_bucket._current_capacity == 0
        assert await token_bucket.acquire(1, block=False)

    @pytest.mark.asyncio
    async def test_rate_cant_go_below_min(self):
        token_bucket = self.create_token_bucket(max_rate=1, min_rate=0.2)
        self.timestamp_sequences.append(1)
        await token_bucket.set_max_rate(0.1)
        assert token_bucket._fill_rate == 0.2
        assert token_bucket._current_capacity == 1
