# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import datetime
from io import BytesIO

import pytest
from dateutil.tz import tzutc

from aiobotocore import response
from aiobotocore.awsrequest import AioAWSResponse
from tests.botocore_tests.unit import BaseResponseTest

pytestmark = pytest.mark.anyio

XMLBODY1 = (
    b'<?xml version="1.0" encoding="UTF-8"?><Error>'
    b'<Code>AccessDenied</Code>'
    b'<Message>Access Denied</Message>'
    b'<RequestId>XXXXXXXXXXXXXXXX</RequestId>'
    b'<HostId>AAAAAAAAAAAAAAAAAAA</HostId>'
    b'</Error>'
)

XMLBODY2 = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b'<Name>mybucket</Name><Prefix></Prefix><Marker></Marker>'
    b'<MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated>'
    b'<Contents><Key>test.png</Key><LastModified>2014-03-01T17:06:40.000Z</LastModified>'
    b'<ETag>&quot;00000000000000000000000000000000&quot;</ETag><Size>6702</Size>'
    b'<Owner><ID>AAAAAAAAAAAAAAAAAAA</ID>'
    b'<DisplayName>dummy</DisplayName></Owner>'
    b'<StorageClass>STANDARD</StorageClass></Contents></ListBucketResult>'
)


class FakeRawResponse(BytesIO):
    async def read(self, amt=-1):
        if amt == -1:  # aiohttp to regular response
            amt = None
        return super().read(amt)


class TestGetResponse(BaseResponseTest):
    maxDiff = None

    async def test_get_response_streaming_ok(self, session):
        headers = {
            'content-type': 'image/png',
            'server': 'AmazonS3',
            'AcceptRanges': 'bytes',
            'transfer-encoding': 'chunked',
            'ETag': '"00000000000000000000000000000000"',
        }
        raw = FakeRawResponse(b'\x89PNG\r\n\x1a\n\x00\x00')

        http_response = AioAWSResponse(None, 200, headers, raw)

        service_model = await session.get_service_model('s3')
        operation_model = service_model.operation_model('GetObject')

        res = await response.get_response(operation_model, http_response)
        assert isinstance(res[1]['Body'], response.StreamingBody)
        assert res[1]['ETag'] == '"00000000000000000000000000000000"'

    async def test_get_response_streaming_ng(self, session):
        headers = {
            'content-type': 'application/xml',
            'date': 'Sat, 08 Mar 2014 12:05:44 GMT',
            'server': 'AmazonS3',
            'transfer-encoding': 'chunked',
            'x-amz-id-2': 'AAAAAAAAAAAAAAAAAAA',
            'x-amz-request-id': 'XXXXXXXXXXXXXXXX',
        }
        raw = FakeRawResponse(XMLBODY1)
        http_response = AioAWSResponse(None, 403, headers, raw)

        service_model = await session.get_service_model('s3')
        operation_model = service_model.operation_model('GetObject')

        self.assert_response_with_subset_metadata(
            (await response.get_response(operation_model, http_response))[1],
            {
                'Error': {'Message': 'Access Denied', 'Code': 'AccessDenied'},
                'ResponseMetadata': {
                    'HostId': 'AAAAAAAAAAAAAAAAAAA',
                    'RequestId': 'XXXXXXXXXXXXXXXX',
                    'HTTPStatusCode': 403,
                },
            },
        )

    async def test_get_response_nonstreaming_ok(self, session):
        headers = {
            'content-type': 'application/xml',
            'date': 'Sun, 09 Mar 2014 02:55:43 GMT',
            'server': 'AmazonS3',
            'transfer-encoding': 'chunked',
            'x-amz-id-2': 'AAAAAAAAAAAAAAAAAAA',
            'x-amz-request-id': 'XXXXXXXXXXXXXXXX',
        }
        raw = FakeRawResponse(XMLBODY1)
        http_response = AioAWSResponse(None, 403, headers, raw)

        service_model = await session.get_service_model('s3')
        operation_model = service_model.operation_model('ListObjects')

        self.assert_response_with_subset_metadata(
            (await response.get_response(operation_model, http_response))[1],
            {
                'ResponseMetadata': {
                    'RequestId': 'XXXXXXXXXXXXXXXX',
                    'HostId': 'AAAAAAAAAAAAAAAAAAA',
                    'HTTPStatusCode': 403,
                },
                'Error': {'Message': 'Access Denied', 'Code': 'AccessDenied'},
            },
        )

    async def test_get_response_nonstreaming_ng(self, session):
        headers = {
            'content-type': 'application/xml',
            'date': 'Sat, 08 Mar 2014 12:05:44 GMT',
            'server': 'AmazonS3',
            'transfer-encoding': 'chunked',
            'x-amz-id-2': 'AAAAAAAAAAAAAAAAAAA',
            'x-amz-request-id': 'XXXXXXXXXXXXXXXX',
        }
        raw = FakeRawResponse(XMLBODY2)
        http_response = AioAWSResponse(None, 200, headers, raw)

        service_model = await session.get_service_model('s3')
        operation_model = service_model.operation_model('ListObjects')

        self.assert_response_with_subset_metadata(
            (await response.get_response(operation_model, http_response))[1],
            {
                'Contents': [
                    {
                        'ETag': '"00000000000000000000000000000000"',
                        'Key': 'test.png',
                        'LastModified': datetime.datetime(
                            2014, 3, 1, 17, 6, 40, tzinfo=tzutc()
                        ),
                        'Owner': {
                            'DisplayName': 'dummy',
                            'ID': 'AAAAAAAAAAAAAAAAAAA',
                        },
                        'Size': 6702,
                        'StorageClass': 'STANDARD',
                    }
                ],
                'IsTruncated': False,
                'Marker': "",
                'MaxKeys': 1000,
                'Name': 'mybucket',
                'Prefix': "",
                'ResponseMetadata': {
                    'RequestId': 'XXXXXXXXXXXXXXXX',
                    'HostId': 'AAAAAAAAAAAAAAAAAAA',
                    'HTTPStatusCode': 200,
                },
            },
        )
