# Copyright 2021 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from io import BytesIO
from unittest import mock

import pytest
from botocore.exceptions import AwsChunkedWrapperError, FlexibleChecksumError
from botocore.httpchecksum import Crc32Checksum
from botocore.model import OperationModel, StringShape, StructureShape

from aiobotocore.awsrequest import AioAWSResponse
from aiobotocore.config import AioConfig
from aiobotocore.httpchecksum import (
    AioAwsChunkedWrapper,
    StreamingChecksumBody,
    apply_request_checksum,
    handle_checksum_body,
)
from aiobotocore.response import StreamingBody
from tests.test_response import AsyncBytesIO


class TestHttpChecksumHandlers:
    def _make_operation_model(
        self,
        http_checksum=None,
        streaming_output=False,
        streaming_input=False,
        required=False,
    ):
        operation = mock.Mock(spec=OperationModel)
        if http_checksum is None:
            http_checksum = {}
        operation.http_checksum = http_checksum
        operation.http_checksum_required = required
        operation.has_streaming_output = streaming_output
        operation.has_streaming_input = streaming_input
        if http_checksum and "requestAlgorithmMember" in http_checksum:
            shape = mock.Mock(spec=StringShape)
            shape.serialization = {"name": "x-amz-request-algorithm"}
            operation.input_shape = mock.Mock(spec=StructureShape)
            operation.input_shape.members = {
                http_checksum["requestAlgorithmMember"]: shape
            }
        return operation

    def _make_http_response(
        self,
        body,
        headers=None,
        context=None,
        streaming=False,
    ):
        if context is None:
            context = {}

        if headers is None:
            headers = {}

        http_response = mock.Mock(spec=AioAWSResponse)
        http_response.raw = AsyncBytesIO(body)

        async def _content_prop():
            return body

        type(http_response).content = mock.PropertyMock(
            side_effect=_content_prop
        )
        http_response.status_code = 200
        http_response.headers = headers
        response_dict = {
            "headers": http_response.headers,
            "status_code": http_response.status_code,
            "context": context,
        }
        if streaming:
            response_dict["body"] = StreamingBody(
                http_response.raw,
                response_dict["headers"].get("content-length"),
            )
        else:
            response_dict["body"] = body
        return http_response, response_dict

    def _build_request(self, body):
        request = {
            "headers": {},
            "body": body,
            "context": {
                "client_config": AioConfig(
                    request_checksum_calculation="when_supported",
                )
            },
            "url": "https://example.com",
        }
        return request

    def test_apply_request_checksum_handles_no_checksum_context(self):
        request = self._build_request(b"")
        apply_request_checksum(request)
        # Build another request and assert the original request is the same
        expected_request = self._build_request(b"")
        assert request["headers"] == expected_request["headers"]
        assert request["body"] == expected_request["body"]
        assert request["url"] == expected_request["url"]

    def test_apply_request_checksum_handles_invalid_context(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "http-trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        with pytest.raises(FlexibleChecksumError):
            apply_request_checksum(request)

    def test_apply_request_checksum_flex_header_bytes(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "header",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        apply_request_checksum(request)
        assert "x-amz-checksum-crc32" in request["headers"]

    def test_apply_request_checksum_flex_header_readable(self):
        request = self._build_request(BytesIO(b""))
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "header",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        apply_request_checksum(request)
        assert "x-amz-checksum-crc32" in request["headers"]

    def test_apply_request_checksum_flex_header_explicit_digest(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "header",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        request["headers"]["x-amz-checksum-crc32"] = "foo"
        apply_request_checksum(request)
        # The checksum should not have been modified
        assert request["headers"]["x-amz-checksum-crc32"] == "foo"

    def test_apply_request_checksum_flex_trailer_bytes(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        apply_request_checksum(request)
        assert "x-amz-checksum-crc32" not in request["headers"]
        assert isinstance(request["body"], AioAwsChunkedWrapper)

    def test_apply_request_checksum_flex_trailer_readable(self):
        request = self._build_request(BytesIO(b""))
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        apply_request_checksum(request)
        assert "x-amz-checksum-crc32" not in request["headers"]
        assert isinstance(request["body"], AioAwsChunkedWrapper)

    def test_apply_request_checksum_flex_header_trailer_explicit_digest(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        request["headers"]["x-amz-checksum-crc32"] = "foo"
        apply_request_checksum(request)
        # The checksum should not have been modified
        assert request["headers"]["x-amz-checksum-crc32"] == "foo"
        # The body should not have been wrapped
        assert isinstance(request["body"], bytes)

    def test_apply_request_checksum_content_encoding_preset(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        request["headers"]["Content-Encoding"] = "foo"
        apply_request_checksum(request)
        # The content encoding should only have been appended
        assert request["headers"]["Content-Encoding"] == "foo,aws-chunked"

    def test_apply_request_checksum_content_encoding_default(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            }
        }
        apply_request_checksum(request)
        assert request["headers"]["Content-Encoding"] == "aws-chunked"

    def test_apply_request_checksum_extra_headers(self):
        request = self._build_request(b"")
        request["context"]["checksum"] = {
            "request_algorithm": {
                "in": "trailer",
                "algorithm": "crc32",
                "name": "x-amz-checksum-crc32",
            },
            "request_algorithm_header": {
                "name": "foo",
                "value": "bar",
            },
        }
        apply_request_checksum(request)
        assert request["headers"]["foo"] == "bar"

    async def test_handle_checksum_body_checksum(self):
        context = {"checksum": {"response_algorithms": ["sha1", "crc32"]}}
        headers = {"x-amz-checksum-crc32": "DUoRhQ=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
        )
        operation_model = self._make_operation_model()
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert body == b"hello world"
        algorithm = response_dict["context"]["checksum"]["response_algorithm"]
        assert algorithm == "crc32"

        headers = {"x-amz-checksum-crc32": "WrOonG=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
        )
        with pytest.raises(FlexibleChecksumError):
            await handle_checksum_body(
                http_response,
                response_dict,
                context,
                operation_model,
            )

        # This header should not be checked, we won't calculate a checksum
        # but a proper body should still come out at the end
        headers = {"x-amz-checksum-foo": "FOO=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
        )
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert body == b"hello world"
        algorithm = response_dict["context"]["checksum"]["response_algorithm"]
        assert algorithm == "crc32"

    async def test_handle_checksum_body_checksum_streaming(self):
        context = {"checksum": {"response_algorithms": ["sha1", "crc32"]}}
        headers = {"x-amz-checksum-crc32": "DUoRhQ=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
            streaming=True,
        )
        operation_model = self._make_operation_model(streaming_output=True)
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert await body.read() == b"hello world"
        algorithm = response_dict["context"]["checksum"]["response_algorithm"]
        assert algorithm == "crc32"

        headers = {"x-amz-checksum-crc32": "WrOonG=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
            streaming=True,
        )
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        with pytest.raises(FlexibleChecksumError):
            await body.read()

        # This header should not be checked, we won't calculate a checksum
        # but a proper body should still come out at the end
        headers = {"x-amz-checksum-foo": "FOOO=="}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
            streaming=True,
        )
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert await body.read() == b"hello world"
        algorithm = response_dict["context"]["checksum"]["response_algorithm"]
        assert algorithm == "crc32"

    async def test_handle_checksum_body_checksum_skip_non_streaming(self):
        context = {"checksum": {"response_algorithms": ["sha1", "crc32"]}}
        # S3 will return checksums over the checksums of parts which are a
        # special case that end with -#. These cannot be validated and are
        # instead skipped
        headers = {"x-amz-checksum-crc32": "FOOO==-123"}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
        )
        operation_model = self._make_operation_model()
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert body == b"hello world"

    async def test_handle_checksum_body_checksum_skip_streaming(self):
        context = {"checksum": {"response_algorithms": ["sha1", "crc32"]}}
        # S3 will return checksums over the checksums of parts which are a
        # special case that end with -#. These cannot be validated and are
        # instead skipped
        headers = {"x-amz-checksum-crc32": "FOOO==-123"}
        http_response, response_dict = self._make_http_response(
            b"hello world",
            headers=headers,
            context=context,
            streaming=True,
        )
        operation_model = self._make_operation_model(streaming_output=True)
        await handle_checksum_body(
            http_response,
            response_dict,
            context,
            operation_model,
        )
        body = response_dict["body"]
        assert await body.read() == b"hello world"


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


class TestStreamingChecksumBody:
    @pytest.fixture(scope="session")
    def raw_bytes(self):
        return b"hello world"

    @pytest.fixture
    def fake_body(self, raw_bytes):
        return AsyncBytesIO(raw_bytes)

    @pytest.fixture
    def make_wrapper(self, fake_body):
        def make_wrapper(checksum):
            return StreamingChecksumBody(
                fake_body,
                None,
                Crc32Checksum(),
                checksum,
            )

        return make_wrapper

    @pytest.fixture
    def wrapper(self, make_wrapper):
        return make_wrapper("DUoRhQ==")

    async def test_basic_read_good(self, raw_bytes, wrapper):
        actual = await wrapper.read()
        assert actual == raw_bytes

    async def test_many_reads_good(self, raw_bytes, wrapper):
        actual = b""
        actual += await wrapper.read(5)
        actual += await wrapper.read(5)
        actual += await wrapper.read(1)
        assert actual == raw_bytes

    async def test_basic_read_bad(self, make_wrapper):
        wrapper = make_wrapper("duorhq==")
        with pytest.raises(FlexibleChecksumError):
            await wrapper.read()

    async def test_many_reads_bad(self, make_wrapper):
        wrapper = make_wrapper("duorhq==")
        await wrapper.read(5)
        await wrapper.read(6)
        # Whole body has been read, next read signals the end of the stream and
        # validates the checksum of the body contents read
        with pytest.raises(FlexibleChecksumError):
            await wrapper.read(1)

    async def test_handles_variable_padding(self, raw_bytes, make_wrapper):
        # This digest is equivalent but with more padding
        wrapper = make_wrapper("DUoRhQ=====")
        actual = await wrapper.read()
        assert actual == raw_bytes

    async def test_iter_raises_error(self, make_wrapper):
        wrapper = make_wrapper("duorhq==")
        with pytest.raises(FlexibleChecksumError):
            async for chunk in wrapper:
                pass
