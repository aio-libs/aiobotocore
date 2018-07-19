import wrapt
from botocore.exceptions import IncompleteReadError


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

    async def read(self, amt=None):
        """Read at most amt bytes from the stream.

        If the amt argument is omitted, read all data.
        """
        # botocore to aiohttp mapping
        chunk = await self.__wrapped__.read(amt if amt is not None else -1)
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

    # TODO: when we move to python >=3.6 we can make this like the sync ver
    def iter_lines(self, chunk_size=1024):
        """Return an iterator to yield lines from the raw stream.

        This is achieved by reading chunk of bytes (of size chunk_size) at a
        time from the raw stream, and then yielding lines from there.
        """
        parent = self

        class _LineIterator:
            def __init__(self):
                self._chunk = None
                self._pending = None
                self._lines = None
                self._finished = False

            def __aiter__(self):
                return self

            async def _get_chunk(self):
                self._chunk = await parent.read(chunk_size)
                if self._chunk == b'':
                    self._finished = True

                if self._pending is not None:
                    self._chunk = self._pending + self._chunk

                self._lines = self._chunk.splitlines()

                if self._lines and self._lines[-1] and self._chunk and \
                        self._lines[-1][-1] == self._chunk[-1]:
                    # We might be in the 'middle' of a line. Hence we keep
                    # the last line as pending.
                    self._pending = self._lines.pop()
                else:
                    self._pending = None

            async def __anext__(self):
                while not self._finished and (
                        self._chunk is None or not self._lines):
                    await self._get_chunk()

                if self._lines:
                    return self._lines.pop(0)

                if self._pending:
                    line = self._pending
                    self._pending = None
                    return line

                raise StopAsyncIteration

        return _LineIterator()

    # TODO: when we move to python >=3.6 we can make this like the sync ver
    def iter_chunks(self, chunk_size=_DEFAULT_CHUNK_SIZE):
        """Return an iterator to yield chunks of chunk_size bytes from the raw
        stream.
        """
        parent = self

        class _ChunkingIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                current_chunk = await parent.read(chunk_size)
                if current_chunk == b"":
                    raise StopAsyncIteration

                return current_chunk

        return _ChunkingIterator()

    def _verify_content_length(self):
        # See: https://github.com/kennethreitz/requests/issues/1855
        # Basically, our http library doesn't do this for us, so we have
        # to do this ourself.
        if self._self_content_length is not None and \
                self._self_amount_read != int(self._self_content_length):
            raise IncompleteReadError(
                actual_bytes=self._self_amount_read,
                expected_bytes=int(self._self_content_length))
