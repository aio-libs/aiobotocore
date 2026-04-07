import asyncio
import io
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import IncompleteReadError

from aiobotocore import response
from aiobotocore.response import AioReadTimeoutError, HttpxStreamingBody


# https://github.com/boto/botocore/blob/develop/tests/unit/test_response.py
async def assert_lines(line_iterator, expected_lines):
    for expected_line in expected_lines:
        line = await line_iterator.__anext__()
        assert line == expected_line

    # We should have exhausted the iterator.
    with pytest.raises(StopAsyncIteration):
        await line_iterator.__anext__()


class AsyncBytesIO(io.BytesIO):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.content = self

    async def read(self, amt=-1):
        if amt == -1:  # aiohttp to regular response
            amt = None
        return super().read(amt)

    async def readinto(self, b):
        return super().readinto(b)


async def _tolist(aiter):
    results = []
    async for item in aiter:
        results.append(item)
    return results


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_wrapper_validates_content_length():
    body = AsyncBytesIO(b'1234567890')
    stream = response.StreamingBody(body, content_length=10)
    assert await stream.read() == b'1234567890'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_with_invalid_length():
    body = AsyncBytesIO(b'123456789')
    stream = response.StreamingBody(body, content_length=10)
    with pytest.raises(IncompleteReadError):
        assert await stream.read(9) == b'123456789'
        # The next read will have nothing returned and raise
        # an IncompleteReadError because we were expectd 10 bytes, not 9.
        await stream.read()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_with_zero_read():
    body = AsyncBytesIO(b'1234567890')
    stream = response.StreamingBody(body, content_length=10)
    chunk = await stream.read(0)
    assert chunk == b''
    assert await stream.read() == b'1234567890'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_with_single_read():
    body = AsyncBytesIO(b'123456789')
    stream = response.StreamingBody(body, content_length=10)
    with pytest.raises(IncompleteReadError):
        await stream.read()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_closes():
    body = AsyncBytesIO(b'1234567890')
    stream = response.StreamingBody(body, content_length=10)
    assert body.closed is False
    stream.close()
    assert body.closed is True


@pytest.mark.moto
@pytest.mark.asyncio
async def test_default_iter_behavior():
    body = AsyncBytesIO(b'a' * 2048)
    stream = response.StreamingBody(body, content_length=2048)
    chunks = await _tolist(stream)
    assert len(chunks) == 2
    assert chunks, [b'a' * 1024 == b'a' * 1024]


@pytest.mark.moto
@pytest.mark.asyncio
async def test_iter_chunks_single_byte():
    body = AsyncBytesIO(b'abcde')
    stream = response.StreamingBody(body, content_length=5)
    chunks = await _tolist(stream.iter_chunks(chunk_size=1))
    assert chunks, [b'a', b'b', b'c', b'd' == b'e']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_iter_chunks_with_leftover():
    body = AsyncBytesIO(b'abcde')
    stream = response.StreamingBody(body, content_length=5)
    chunks = await _tolist(stream.iter_chunks(chunk_size=2))
    assert chunks, [b'ab', b'cd' == b'e']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_iter_chunks_single_chunk():
    body = AsyncBytesIO(b'abcde')
    stream = response.StreamingBody(body, content_length=5)
    chunks = await _tolist(stream.iter_chunks(chunk_size=1024))
    assert chunks == [b'abcde']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_line_iterator():
    body = AsyncBytesIO(b'1234567890\n1234567890\n12345')
    stream = response.StreamingBody(body, content_length=27)
    await assert_lines(
        stream.iter_lines(),
        [b'1234567890', b'1234567890', b'12345'],
    )


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_line_iterator_ends_newline():
    body = AsyncBytesIO(b'1234567890\n1234567890\n12345\n')
    stream = response.StreamingBody(body, content_length=28)
    await assert_lines(
        stream.iter_lines(),
        [b'1234567890', b'1234567890', b'12345'],
    )


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_line_iter_chunk_sizes():
    for chunk_size in range(1, 30):
        body = AsyncBytesIO(b'1234567890\n1234567890\n12345')
        stream = response.StreamingBody(body, content_length=27)
        await assert_lines(
            stream.iter_lines(chunk_size),
            [b'1234567890', b'1234567890', b'12345'],
        )


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_is_an_iterator():
    body = AsyncBytesIO(b'a' * 1024 + b'b' * 1024 + b'c' * 2)
    stream = response.StreamingBody(body, content_length=2050)
    assert b'a' * 1024 == await stream.__anext__()
    assert b'b' * 1024 == await stream.__anext__()
    assert b'c' * 2 == await stream.__anext__()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_line_abstruse_newline_standard():
    for chunk_size in range(1, 30):
        body = AsyncBytesIO(b'1234567890\r\n1234567890\r\n12345\r\n')
        stream = response.StreamingBody(body, content_length=31)
        await assert_lines(
            stream.iter_lines(chunk_size),
            [b'1234567890', b'1234567890', b'12345'],
        )


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_line_empty_body():
    stream = response.StreamingBody(
        AsyncBytesIO(b''),
        content_length=0,
    )
    await assert_lines(stream.iter_lines(), [])


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_readinto():
    body = AsyncBytesIO(b"123456789")
    stream = response.StreamingBody(body, content_length=10)
    chunk = bytearray(b"\x00\x00\x00\x00\x00")
    assert 5 == await stream.readinto(chunk)
    assert chunk == bytearray(b"\x31\x32\x33\x34\x35")
    assert 4 == await stream.readinto(chunk)
    assert chunk == bytearray(b"\x36\x37\x38\x39\x35")


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_readinto_with_invalid_length():
    body = AsyncBytesIO(b"12")
    stream = response.StreamingBody(body, content_length=9)
    chunk = bytearray(b"\xde\xad\xbe\xef")
    assert 2 == await stream.readinto(chunk)
    assert chunk == bytearray(b"\x31\x32\xbe\xef")
    with pytest.raises(IncompleteReadError):
        await stream.readinto(chunk)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_readinto_with_empty_buffer():
    body = AsyncBytesIO(b"12")
    stream = response.StreamingBody(body, content_length=9)
    chunk = bytearray(b"")
    assert 0 == await stream.readinto(chunk)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_readinto_with_timeout():
    class TimeoutBody:
        def __init__(self, *args, **kwargs):
            self.content = MagicMock()
            self.content.read = self.read
            self.url = ""

        async def read(self, n: int):
            raise asyncio.TimeoutError()

    stream = response.StreamingBody(TimeoutBody(), content_length=9)
    with pytest.raises(AioReadTimeoutError):
        chunk = bytearray(b"\x00\x00\x00\x00\x00")
        await stream.readinto(chunk)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_streaming_body_raw_stream():
    body = AsyncBytesIO(b'1234567890')
    stream = response.StreamingBody(body, content_length=10)
    assert stream.raw_stream is body


# -- HttpxStreamingBody tests --


class MockHttpxResponse:
    """Mock that simulates httpx.Response for testing HttpxStreamingBody."""

    def __init__(self, data: bytes, chunk_size: int = 1024):
        self._data = data
        self._chunk_size = chunk_size
        self._closed = False

    async def aiter_bytes(self):
        offset = 0
        while offset < len(self._data):
            yield self._data[offset : offset + self._chunk_size]
            offset += self._chunk_size

    async def aclose(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_read_all():
    body = MockHttpxResponse(b'1234567890')
    stream = HttpxStreamingBody(body, content_length=10)
    assert await stream.read() == b'1234567890'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_read_with_amt():
    body = MockHttpxResponse(b'1234567890', chunk_size=4)
    stream = HttpxStreamingBody(body, content_length=10)
    assert await stream.read(5) == b'12345'
    assert await stream.read(5) == b'67890'
    assert await stream.read(5) == b''


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_read_zero():
    body = MockHttpxResponse(b'1234567890')
    stream = HttpxStreamingBody(body, content_length=10)
    assert await stream.read(0) == b''
    assert await stream.read() == b'1234567890'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_content_length_validation():
    body = MockHttpxResponse(b'123456789')
    stream = HttpxStreamingBody(body, content_length=10)
    with pytest.raises(IncompleteReadError):
        await stream.read()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_content_length_validation_chunked():
    body = MockHttpxResponse(b'123456789')
    stream = HttpxStreamingBody(body, content_length=10)
    assert await stream.read(9) == b'123456789'
    with pytest.raises(IncompleteReadError):
        await stream.read()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_readinto():
    body = MockHttpxResponse(b'123456789', chunk_size=4)
    stream = HttpxStreamingBody(body, content_length=9)
    chunk = bytearray(b'\x00\x00\x00\x00\x00')
    assert 5 == await stream.readinto(chunk)
    assert chunk == bytearray(b'\x31\x32\x33\x34\x35')
    assert 4 == await stream.readinto(chunk)
    assert chunk == bytearray(b'\x36\x37\x38\x39\x35')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_readinto_with_invalid_length():
    body = MockHttpxResponse(b'12')
    stream = HttpxStreamingBody(body, content_length=9)
    chunk = bytearray(b'\xde\xad\xbe\xef')
    assert 2 == await stream.readinto(chunk)
    assert chunk == bytearray(b'\x31\x32\xbe\xef')
    with pytest.raises(IncompleteReadError):
        await stream.readinto(chunk)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_readinto_empty_buffer():
    body = MockHttpxResponse(b'12')
    stream = HttpxStreamingBody(body, content_length=2)
    chunk = bytearray(b'')
    assert 0 == await stream.readinto(chunk)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_iter_chunks():
    body = MockHttpxResponse(b'abcde', chunk_size=2)
    stream = HttpxStreamingBody(body, content_length=5)
    chunks = await _tolist(stream.iter_chunks(chunk_size=2))
    assert chunks == [b'ab', b'cd', b'e']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_default_iter():
    body = MockHttpxResponse(b'a' * 2048, chunk_size=4096)
    stream = HttpxStreamingBody(body, content_length=2048)
    chunks = await _tolist(stream)
    assert len(chunks) == 2
    assert chunks[0] == b'a' * 1024
    assert chunks[1] == b'a' * 1024


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_iter_lines():
    body = MockHttpxResponse(b'line1\nline2\nline3')
    stream = HttpxStreamingBody(body, content_length=17)
    lines = [line async for line in stream.iter_lines()]
    assert lines == [b'line1', b'line2', b'line3']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_readlines():
    body = MockHttpxResponse(b'line1\nline2\nline3')
    stream = HttpxStreamingBody(body, content_length=17)
    lines = await stream.readlines()
    assert lines == [b'line1', b'line2', b'line3']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_tell():
    body = MockHttpxResponse(b'1234567890', chunk_size=4)
    stream = HttpxStreamingBody(body, content_length=10)
    assert stream.tell() == 0
    await stream.read(5)
    assert stream.tell() == 5
    await stream.read()
    assert stream.tell() == 10


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_readable():
    body = MockHttpxResponse(b'12345')
    stream = HttpxStreamingBody(body, content_length=5)
    assert stream.readable()
    await stream.read()
    assert not stream.readable()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_close():
    body = MockHttpxResponse(b'12345')
    stream = HttpxStreamingBody(body, content_length=5)
    assert not body.closed
    await stream.close()
    assert body.closed


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_async_context_manager():
    body = MockHttpxResponse(b'12345')
    async with HttpxStreamingBody(body, content_length=5) as stream:
        data = await stream.read()
        assert data == b'12345'
    assert body.closed


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_raw_stream():
    body = MockHttpxResponse(b'12345')
    stream = HttpxStreamingBody(body, content_length=5)
    assert stream.raw_stream is body


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_is_async_iterator():
    body = MockHttpxResponse(b'a' * 1024 + b'b' * 1024 + b'c' * 2)
    stream = HttpxStreamingBody(body, content_length=2050)
    assert b'a' * 1024 == await stream.__anext__()
    assert b'b' * 1024 == await stream.__anext__()
    assert b'c' * 2 == await stream.__anext__()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_httpx_small_chunks_buffering():
    """Test that buffering works correctly when internal chunks are smaller
    than requested read size."""
    body = MockHttpxResponse(b'0123456789', chunk_size=3)
    stream = HttpxStreamingBody(body, content_length=10)
    # chunk_size=3 means internal chunks are [012, 345, 678, 9]
    # but we request 5 bytes at a time
    assert await stream.read(5) == b'01234'
    assert await stream.read(5) == b'56789'
    assert await stream.read(5) == b''
