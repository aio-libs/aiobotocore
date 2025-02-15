from io import BytesIO

import pytest
from botocore.httpchecksum import AwsChunkedWrapperError, Crc32Checksum

from aiobotocore.httpchecksum import AioAwsChunkedWrapper


class TestAwsChunkedWrapper:
    async def test_single_chunk_body(self):
        # Test a small body that fits in a single chunk
        bytes = BytesIO(b"abcdefghijklmnopqrstuvwxyz")
        wrapper = AioAwsChunkedWrapper(bytes)
        body = await wrapper.read()
        expected = b"1a\r\n" b"abcdefghijklmnopqrstuvwxyz\r\n" b"0\r\n\r\n"
        assert body == expected

    async def test_multi_chunk_body(self):
        # Test a body that requires multiple chunks
        bytes = BytesIO(b"abcdefghijklmnopqrstuvwxyz")
        wrapper = AioAwsChunkedWrapper(bytes, chunk_size=10)
        body = await wrapper.read()
        expected = (
            b"a\r\n"
            b"abcdefghij\r\n"
            b"a\r\n"
            b"klmnopqrst\r\n"
            b"6\r\n"
            b"uvwxyz\r\n"
            b"0\r\n\r\n"
        )
        assert body == expected

    async def test_read_returns_less_data(self):
        class OneLessBytesIO(BytesIO):
            def read(self, size=-1):
                # Return 1 less byte than was asked for
                return super().read(size - 1)

        bytes = OneLessBytesIO(b"abcdefghijklmnopqrstuvwxyz")
        wrapper = AioAwsChunkedWrapper(bytes, chunk_size=10)
        body = await wrapper.read()
        # NOTE: This particular body is not important, but it is important that
        # the actual size of the chunk matches the length sent which may not
        # always be the configured chunk_size if the read does not return that
        # much data.
        expected = (
            b"9\r\n"
            b"abcdefghi\r\n"
            b"9\r\n"
            b"jklmnopqr\r\n"
            b"8\r\n"
            b"stuvwxyz\r\n"
            b"0\r\n\r\n"
        )
        assert body == expected

    async def test_single_chunk_body_with_checksum(self):
        wrapper = AioAwsChunkedWrapper(
            BytesIO(b"hello world"),
            checksum_cls=Crc32Checksum,
            checksum_name="checksum",
        )
        body = await wrapper.read()
        expected = (
            b"b\r\n" b"hello world\r\n" b"0\r\n" b"checksum:DUoRhQ==\r\n\r\n"
        )
        assert body == expected

    async def test_multi_chunk_body_with_checksum(self):
        wrapper = AioAwsChunkedWrapper(
            BytesIO(b"hello world"),
            chunk_size=5,
            checksum_cls=Crc32Checksum,
            checksum_name="checksum",
        )
        body = await wrapper.read()
        expected = (
            b"5\r\n"
            b"hello\r\n"
            b"5\r\n"
            b" worl\r\n"
            b"1\r\n"
            b"d\r\n"
            b"0\r\n"
            b"checksum:DUoRhQ==\r\n\r\n"
        )
        assert body == expected

    async def test_multi_chunk_body_with_checksum_iter(self):
        wrapper = AioAwsChunkedWrapper(
            BytesIO(b"hello world"),
            chunk_size=5,
            checksum_cls=Crc32Checksum,
            checksum_name="checksum",
        )
        expected_chunks = [
            b"5\r\nhello\r\n",
            b"5\r\n worl\r\n",
            b"1\r\nd\r\n",
            b"0\r\nchecksum:DUoRhQ==\r\n\r\n",
        ]
        assert expected_chunks == [chunk async for chunk in wrapper]

    async def test_wrapper_can_be_reset(self):
        wrapper = AioAwsChunkedWrapper(
            BytesIO(b"hello world"),
            chunk_size=5,
            checksum_cls=Crc32Checksum,
            checksum_name="checksum",
        )
        first_read = await wrapper.read()
        assert b"" == await wrapper.read()
        wrapper.seek(0)
        second_read = await wrapper.read()
        assert first_read == second_read
        assert b"checksum:DUoRhQ==" in first_read

    def test_wrapper_can_only_seek_to_start(self):
        wrapper = AioAwsChunkedWrapper(BytesIO())
        with pytest.raises(AwsChunkedWrapperError):
            wrapper.seek(1)
        with pytest.raises(AwsChunkedWrapperError):
            wrapper.seek(0, whence=1)
        with pytest.raises(AwsChunkedWrapperError):
            wrapper.seek(1, whence=2)
