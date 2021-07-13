import asyncio

import wrapt
from botocore.exceptions import IncompleteReadError, ReadTimeoutError
from aiobotocore import parsers


class AioReadTimeoutError(ReadTimeoutError, asyncio.TimeoutError):
    pass


class StreamingBody(wrapt.ObjectProxy):
    """Wrapper class for an http response body.

    This provides a few additional conveniences that do not exist
    in the urllib3 model:

        * Set the timeout on the socket (i.e read() timeouts)
        * Auto validation of content length, if the amount of bytes
          we read does not match the content length, an exception
          is raised.
    """

    _DEFAULT_CHUNK_SIZE = 1024

    def __init__(self, raw_stream, content_length):
        super().__init__(raw_stream)
        self._self_content_length = content_length
        self._self_amount_read = 0

    # https://github.com/GrahamDumpleton/wrapt/issues/73
    async def __aenter__(self):
        return await self.__wrapped__.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    # NOTE: set_socket_timeout was only for when requests didn't support
    #       read timeouts, so not needed

    def tell(self):
        return self._self_amount_read

    async def read(self, amt=None):
        """Read at most amt bytes from the stream.

        If the amt argument is omitted, read all data.
        """
        # botocore to aiohttp mapping
        try:
            chunk = await self.__wrapped__.read(amt if amt is not None else -1)
        except asyncio.TimeoutError as e:
            raise AioReadTimeoutError(endpoint_url=self.__wrapped__.url,
                                      error=e)

        self._self_amount_read += len(chunk)
        if amt is None or (not chunk and amt > 0):
            # If the server sends empty contents or
            # we ask to read all of the contents, then we know
            # we need to verify the content length.
            self._verify_content_length()
        return chunk

    def __aiter__(self):
        """Return an iterator to yield 1k chunks from the raw stream.
        """
        return self.iter_chunks(self._DEFAULT_CHUNK_SIZE)

    async def __anext__(self):
        """Return the next 1k chunk from the raw stream.
        """
        current_chunk = await self.read(self._DEFAULT_CHUNK_SIZE)
        if current_chunk:
            return current_chunk
        raise StopAsyncIteration

    anext = __anext__

    async def iter_lines(self, chunk_size=1024, keepends=False):
        """Return an iterator to yield lines from the raw stream.

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
        """Return an iterator to yield chunks of chunk_size bytes from the raw
        stream.
        """
        while True:
            current_chunk = await self.read(chunk_size)
            if current_chunk == b"":
                break
            yield current_chunk

    def _verify_content_length(self):
        # See: https://github.com/kennethreitz/requests/issues/1855
        # Basically, our http library doesn't do this for us, so we have
        # to do this ourself.
        if self._self_content_length is not None and \
                self._self_amount_read != int(self._self_content_length):
            raise IncompleteReadError(
                actual_bytes=self._self_amount_read,
                expected_bytes=int(self._self_content_length))


async def get_response(operation_model, http_response):
    protocol = operation_model.metadata['protocol']
    response_dict = {
        'headers': http_response.headers,
        'status_code': http_response.status_code,
    }
    # TODO: Unfortunately, we have to have error logic here.
    # If it looks like an error, in the streaming response case we
    # need to actually grab the contents.
    if response_dict['status_code'] >= 300:
        response_dict['body'] = http_response.content
    elif operation_model.has_streaming_output:
        response_dict['body'] = StreamingBody(
            http_response.raw, response_dict['headers'].get('content-length'))
    else:
        response_dict['body'] = http_response.content

    parser = parsers.create_parser(protocol)
    if asyncio.iscoroutinefunction(parser.parse):
        parsed = await parser.parse(
            response_dict, operation_model.output_shape)
    else:
        parsed = parser.parse(
            response_dict, operation_model.output_shape)
    return http_response, parsed
