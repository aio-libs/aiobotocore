import asyncio

import aiohttp
import aiohttp.client_exceptions
from botocore.response import (
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)

from aiobotocore import parsers


class AioReadTimeoutError(ReadTimeoutError, asyncio.TimeoutError):
    pass


class StreamingBody:
    """Async wrapper class for an HTTP response body (aiohttp backend).

    Provides a backend-agnostic async streaming API with:
        * Chunked reading via read(amt)
        * Content length validation
        * Async iteration, line iteration, and chunk iteration
        * Access to the underlying raw stream via .raw_stream
    """

    _DEFAULT_CHUNK_SIZE = 1024

    def __init__(self, raw_stream, content_length):
        self._raw_stream = raw_stream
        self._content_length = content_length
        self._amount_read = 0

    @property
    def raw_stream(self):
        """Access the underlying raw HTTP response object."""
        return self._raw_stream

    async def __aenter__(self):
        return await self._raw_stream.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._raw_stream.__aexit__(exc_type, exc_val, exc_tb)

    def readable(self):
        return not self._raw_stream.content.at_eof()

    async def read(self, amt=None):
        """Read at most amt bytes from the stream.

        If the amt argument is omitted, read all data.
        """
        try:
            chunk = await self._raw_stream.content.read(
                amt if amt is not None else -1
            )
        except asyncio.TimeoutError as e:
            raise AioReadTimeoutError(
                endpoint_url=self._raw_stream.url, error=e
            )
        except aiohttp.client_exceptions.ClientConnectionError as e:
            raise ResponseStreamingError(error=e)

        self._amount_read += len(chunk)
        if amt is None or (not chunk and amt > 0):
            self._verify_content_length()
        return chunk

    async def readinto(self, b: bytearray):
        """Read bytes into a pre-allocated, writable bytes-like object b,
        and return the number of bytes read.
        """
        try:
            chunk = await self._raw_stream.content.read(len(b))
            amount_read = len(chunk)
            b[:amount_read] = chunk

        except asyncio.TimeoutError as e:
            raise AioReadTimeoutError(
                endpoint_url=self._raw_stream.url, error=e
            )
        except aiohttp.client_exceptions.ClientConnectionError as e:
            raise ResponseStreamingError(error=e)

        self._amount_read += amount_read
        if amount_read == 0 and len(b) > 0:
            self._verify_content_length()
        return amount_read

    async def readlines(self):
        lines = [line async for line in self.iter_lines()]
        return lines

    def __aiter__(self):
        """Return an iterator to yield 1k chunks from the raw stream."""
        return self.iter_chunks(self._DEFAULT_CHUNK_SIZE)

    async def __anext__(self):
        """Return the next 1k chunk from the raw stream."""
        current_chunk = await self.read(self._DEFAULT_CHUNK_SIZE)
        if current_chunk:
            return current_chunk
        raise StopAsyncIteration

    anext = __anext__

    async def iter_lines(self, chunk_size=_DEFAULT_CHUNK_SIZE, keepends=False):
        """Return an async iterator to yield lines from the raw stream.

        This is achieved by reading chunk of bytes (of size chunk_size) at a
        time from the raw stream, and then yielding lines from there.
        """
        pending = b''
        async for chunk in self.iter_chunks(chunk_size):
            lines = (pending + chunk).splitlines(True)
            for line in lines[:-1]:
                yield line.splitlines(keepends)[0]
            pending = lines[-1]
        if pending:
            yield pending.splitlines(keepends)[0]

    async def iter_chunks(self, chunk_size=_DEFAULT_CHUNK_SIZE):
        """Return an async iterator to yield chunks of chunk_size bytes
        from the raw stream.
        """
        while True:
            current_chunk = await self.read(chunk_size)
            if current_chunk == b"":
                break
            yield current_chunk

    def _verify_content_length(self):
        if self._content_length is not None and self._amount_read != int(
            self._content_length
        ):
            raise IncompleteReadError(
                actual_bytes=self._amount_read,
                expected_bytes=int(self._content_length),
            )

    def tell(self):
        return self._amount_read

    def close(self):
        self._raw_stream.close()


class HttpxStreamingBody:
    """Async wrapper class for an HTTP response body (httpx backend).

    Provides the same API as StreamingBody but backed by httpx.Response,
    using internal buffering to support read(amt).
    """

    _DEFAULT_CHUNK_SIZE = 1024

    def __init__(self, raw_stream, content_length=None):
        self._raw_stream = raw_stream
        self._content_length = content_length
        self._amount_read = 0
        self._buffer = b''
        self._stream_iter = None
        self._stream_exhausted = False

    @property
    def raw_stream(self):
        """Access the underlying raw httpx.Response object."""
        return self._raw_stream

    def _ensure_stream(self):
        if self._stream_iter is None:
            self._stream_iter = self._raw_stream.aiter_bytes().__aiter__()

    async def _fill_buffer(self, min_bytes):
        """Fill internal buffer until it has at least min_bytes or stream
        is exhausted.
        """
        self._ensure_stream()
        while len(self._buffer) < min_bytes and not self._stream_exhausted:
            try:
                chunk = await self._stream_iter.__anext__()
                self._buffer += chunk
            except StopAsyncIteration:
                self._stream_exhausted = True

    async def read(self, amt=None):
        """Read at most amt bytes from the stream.

        If the amt argument is omitted, read all data.
        """
        self._ensure_stream()

        if amt is None:
            # Read all remaining data
            chunks = [self._buffer]
            self._buffer = b''
            try:
                async for chunk in self._stream_iter:
                    chunks.append(chunk)
            except StopAsyncIteration:
                pass
            self._stream_exhausted = True
            result = b''.join(chunks)
        elif amt == 0:
            return b''
        else:
            await self._fill_buffer(amt)
            result = self._buffer[:amt]
            self._buffer = self._buffer[amt:]

        self._amount_read += len(result)
        if amt is None or (not result and amt > 0):
            self._verify_content_length()
        return result

    async def readinto(self, b: bytearray):
        """Read bytes into a pre-allocated, writable bytes-like object b,
        and return the number of bytes read.
        """
        if len(b) == 0:
            return 0

        await self._fill_buffer(len(b))
        chunk = self._buffer[: len(b)]
        amount_read = len(chunk)
        b[:amount_read] = chunk
        self._buffer = self._buffer[amount_read:]

        self._amount_read += amount_read
        if amount_read == 0 and len(b) > 0:
            self._verify_content_length()
        return amount_read

    async def readlines(self):
        lines = [line async for line in self.iter_lines()]
        return lines

    def __aiter__(self):
        """Return an iterator to yield 1k chunks from the raw stream."""
        return self.iter_chunks(self._DEFAULT_CHUNK_SIZE)

    async def __anext__(self):
        """Return the next 1k chunk from the raw stream."""
        current_chunk = await self.read(self._DEFAULT_CHUNK_SIZE)
        if current_chunk:
            return current_chunk
        raise StopAsyncIteration

    anext = __anext__

    async def iter_lines(self, chunk_size=_DEFAULT_CHUNK_SIZE, keepends=False):
        """Return an async iterator to yield lines from the raw stream."""
        pending = b''
        async for chunk in self.iter_chunks(chunk_size):
            lines = (pending + chunk).splitlines(True)
            for line in lines[:-1]:
                yield line.splitlines(keepends)[0]
            pending = lines[-1]
        if pending:
            yield pending.splitlines(keepends)[0]

    async def iter_chunks(self, chunk_size=_DEFAULT_CHUNK_SIZE):
        """Return an async iterator to yield chunks of chunk_size bytes
        from the raw stream.
        """
        while True:
            current_chunk = await self.read(chunk_size)
            if current_chunk == b"":
                break
            yield current_chunk

    def _verify_content_length(self):
        if self._content_length is not None and self._amount_read != int(
            self._content_length
        ):
            raise IncompleteReadError(
                actual_bytes=self._amount_read,
                expected_bytes=int(self._content_length),
            )

    def tell(self):
        return self._amount_read

    def readable(self):
        return bool(self._buffer) or not self._stream_exhausted

    async def close(self):
        await self._raw_stream.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


async def get_response(operation_model, http_response):
    protocol = operation_model.service_model.resolved_protocol
    response_dict = {
        'headers': http_response.headers,
        'status_code': http_response.status_code,
    }
    # TODO: Unfortunately, we have to have error logic here.
    # If it looks like an error, in the streaming response case we
    # need to actually grab the contents.
    if response_dict['status_code'] >= 300:
        response_dict['body'] = await http_response.content
    elif operation_model.has_streaming_output:
        response_dict['body'] = StreamingBody(
            http_response.raw, response_dict['headers'].get('content-length')
        )
    else:
        response_dict['body'] = await http_response.content

    parser = parsers.create_parser(protocol)
    parsed = await parser.parse(response_dict, operation_model.output_shape)
    return http_response, parsed
