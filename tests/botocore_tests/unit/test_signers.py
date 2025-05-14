# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
from datetime import timezone
from unittest import mock

import botocore.auth
import pytest
from botocore.awsrequest import AWSRequest
from botocore.exceptions import (
    NoRegionError,
    ParamValidationError,
    UnknownClientMethodError,
    UnknownSignatureVersionError,
)
from botocore.model import ServiceId

import aiobotocore.credentials
import aiobotocore.session
import aiobotocore.signers
from tests.botocore_tests import assert_url_equal

DATE = datetime.datetime(2024, 11, 7, 17, 39, 33, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    'aws_auth',
    [{'aws_secret_access_key': 'skid', 'aws_access_key_id': 'akid'}],
)
async def test_signers_generate_db_auth_token(rds_client):
    hostname = 'prod-instance.us-east-1.rds.amazonaws.com'
    port = 3306
    username = 'someusername'
    clock = datetime.datetime(2016, 11, 7, 17, 39, 33, tzinfo=timezone.utc)

    with mock.patch('datetime.datetime') as dt:
        dt.utcnow.return_value = clock
        result = await aiobotocore.signers.generate_db_auth_token(
            rds_client, hostname, port, username
        )

        result2 = await rds_client.generate_db_auth_token(
            hostname, port, username
        )

    expected_result = (
        'prod-instance.us-east-1.rds.amazonaws.com:3306/?Action=connect'
        '&DBUser=someusername&X-Amz-Algorithm=AWS4-HMAC-SHA256'
        '&X-Amz-Date=20161107T173933Z&X-Amz-SignedHeaders=host'
        '&X-Amz-Expires=900&X-Amz-Credential=akid%2F20161107%2F'
        'us-east-1%2Frds-db%2Faws4_request&X-Amz-Signature'
        '=d1138cdbc0ca63eec012ec0fc6c2267e03642168f5884a7795320d4c18374c61'
    )

    assert_url_equal('http://' + result, 'http://' + expected_result)

    assert result2 == result


class TestDSQLGenerateDBAuthToken:
    @pytest.fixture(scope="session")
    def hostname(self):
        return 'test.dsql.us-east-1.on.aws'

    @pytest.fixture(scope="session")
    def action(self):
        return 'DbConnect'

    @pytest.fixture
    async def client(self, session):
        async with session.create_client(
            'dsql',
            region_name='us-east-1',
            aws_access_key_id='ACCESS_KEY',
            aws_secret_access_key='SECRET_KEY',
            aws_session_token="SESSION_TOKEN",
        ) as client:
            yield client

    async def test_dsql_generate_db_auth_token(
        self, client, hostname, action, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers._dsql_generate_db_auth_token(
            client, hostname, action
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnect'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '57fe03e060348aaa21405c239bf02572bbc911076e94dcd65c12ae569dd8fcf4'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_connect_auth_token(
        self, client, hostname, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers.dsql_generate_db_connect_auth_token(
            client, hostname
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnect'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '57fe03e060348aaa21405c239bf02572bbc911076e94dcd65c12ae569dd8fcf4'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_connect_admin_auth_token(
        self, client, hostname, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers.dsql_generate_db_connect_admin_auth_token(
            client, hostname
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnectAdmin'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '5ac084bc7cabccc19a52a5d1b5c24b50d3ce143f43b659bd484c91aaf555e190'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_auth_token_invalid_action(
        self, client, hostname
    ):
        with pytest.raises(ParamValidationError):
            await aiobotocore.signers._dsql_generate_db_auth_token(
                client, hostname, "FooBar"
            )


# From class TestSigner
@pytest.fixture
async def base_signer_setup() -> dict:
    emitter = mock.AsyncMock()
    emitter.emit_until_response.return_value = (None, None)
    credentials = aiobotocore.credentials.AioCredentials('key', 'secret')

    signer = aiobotocore.signers.AioRequestSigner(
        ServiceId('service_name'),
        'region_name',
        'signing_name',
        'v4',
        credentials,
        emitter,
    )
    return {
        'credentials': credentials,
        'emitter': emitter,
        'signer': signer,
        'fixed_credentials': await credentials.get_frozen_credentials(),
        'request': AWSRequest(),
    }


@pytest.fixture
async def base_signer_setup_s3v4() -> dict:
    emitter = mock.AsyncMock()
    emitter.emit_until_response.return_value = (None, None)
    credentials = aiobotocore.credentials.AioCredentials('key', 'secret')

    request_signer = aiobotocore.signers.AioRequestSigner(
        ServiceId('service_name'),
        'region_name',
        'signing_name',
        's3v4',
        credentials,
        emitter,
    )
    signer = aiobotocore.signers.AioS3PostPresigner(request_signer)

    return {
        'credentials': credentials,
        'emitter': emitter,
        'signer': signer,
        'fixed_credentials': await credentials.get_frozen_credentials(),
        'request': AWSRequest(),
    }


# From class TestGenerateUrl
async def test_signers_generate_presigned_urls():
    with mock.patch(
        'aiobotocore.signers.AioRequestSigner.generate_presigned_url'
    ) as cls_gen_presigned_url_mock:
        session = aiobotocore.session.get_session()
        async with session.create_client(
            's3',
            region_name='us-east-1',
            aws_access_key_id='lalala',
            aws_secret_access_key='lalala',
            aws_session_token='lalala',
        ) as client:
            # Uses HEAD as it covers more lines :)
            await client.generate_presigned_url(
                'get_object',
                Params={'Bucket': 'mybucket', 'Key': 'mykey'},
                HttpMethod='HEAD',
            )

            ref_request_dict = {
                'body': b'',
                'url': 'https://mybucket.s3.amazonaws.com/mykey',
                'headers': {},
                'query_string': {},
                'url_path': '/mykey',
                'method': 'HEAD',
                'context': mock.ANY,
                'auth_path': '/mybucket/mykey',
            }

            cls_gen_presigned_url_mock.assert_called_with(
                request_dict=ref_request_dict,
                expires_in=3600,
                operation_name='GetObject',
            )

            cls_gen_presigned_url_mock.reset_mock()

            with pytest.raises(UnknownClientMethodError):
                await client.generate_presigned_url('lalala')


async def test_signers_generate_presigned_post():
    with mock.patch(
        'aiobotocore.signers.AioS3PostPresigner.generate_presigned_post'
    ) as cls_gen_presigned_url_mock:
        session = aiobotocore.session.get_session()
        async with session.create_client(
            's3',
            region_name='us-east-1',
            aws_access_key_id='lalala',
            aws_secret_access_key='lalala',
            aws_session_token='lalala',
        ) as client:
            await client.generate_presigned_post(
                'somebucket', 'someprefix/key'
            )

            cls_gen_presigned_url_mock.assert_called_once()

            cls_gen_presigned_url_mock.reset_mock()

            await client.generate_presigned_post(
                'somebucket',
                'someprefix/${filename}',
                {'some': 'fields'},
                [{'acl': 'public-read'}],
            )

            cls_gen_presigned_url_mock.assert_called_once()

            cls_gen_presigned_url_mock.reset_mock()

            with pytest.raises(UnknownClientMethodError):
                await client.generate_presigned_url('lalala')


async def test_testsigner_get_auth(base_signer_setup: dict):
    auth_cls = mock.Mock()
    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'v4': auth_cls}):
        signer = base_signer_setup['signer']
        auth = await signer.get_auth('service_name', 'region_name')

        assert auth_cls.return_value is auth
        auth_cls.assert_called_with(
            credentials=base_signer_setup['fixed_credentials'],
            service_name='service_name',
            region_name='region_name',
        )


async def test_testsigner_region_required_for_sig4(base_signer_setup: dict):
    signer = aiobotocore.signers.AioRequestSigner(
        ServiceId('service_name'),
        None,
        'signing_name',
        'v4',
        base_signer_setup['credentials'],
        base_signer_setup['emitter'],
    )

    with pytest.raises(NoRegionError):
        await signer.sign('operation_name', base_signer_setup['request'])


async def test_testsigner_custom_sign_version(base_signer_setup: dict):
    signer = base_signer_setup['signer']
    with pytest.raises(UnknownSignatureVersionError):
        await signer.get_auth(
            'service_name', 'region_name', signature_version='bad'
        )


async def test_testsigner_choose_signer_override(base_signer_setup: dict):
    auth_cls = mock.Mock()
    auth_cls.REQUIRES_REGION = False
    base_signer_setup['emitter'].emit_until_response.return_value = (
        None,
        'custom',
    )

    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'custom': auth_cls}):
        signer = base_signer_setup['signer']
        request = base_signer_setup['request']
        await signer.sign('operation_name', request)

        fixed_credentials = base_signer_setup['fixed_credentials']
        auth_cls.assert_called_with(credentials=fixed_credentials)
        auth_cls.return_value.add_auth.assert_called_with(request)


async def test_testsigner_generate_presigned_url(base_signer_setup: dict):
    auth_cls = mock.Mock()
    auth_cls.REQUIRES_REGION = True

    request_dict = {
        'headers': {},
        'url': 'https://foo.com',
        'body': b'',
        'url_path': '/',
        'method': 'GET',
        'context': {},
    }

    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'v4-query': auth_cls}):
        signer = base_signer_setup['signer']
        presigned_url = await signer.generate_presigned_url(
            request_dict, operation_name='operation_name'
        )

    auth_cls.assert_called_with(
        credentials=base_signer_setup['fixed_credentials'],
        region_name='region_name',
        service_name='signing_name',
        expires=3600,
    )
    assert presigned_url == 'https://foo.com'


# From class TestGeneratePresignedPost
async def test_testsigner_generate_presigned_post(
    base_signer_setup_s3v4: dict,
):
    auth_cls = mock.Mock()
    auth_cls.REQUIRES_REGION = True

    request_dict = {
        'headers': {},
        'url': 'https://s3.amazonaws.com/mybucket',
        'body': b'',
        'url_path': '/',
        'method': 'POST',
        'context': {},
    }

    with mock.patch.dict(
        botocore.auth.AUTH_TYPE_MAPS, {'s3v4-presign-post': auth_cls}
    ):
        signer = base_signer_setup_s3v4['signer']
        presigned_url = await signer.generate_presigned_post(
            request_dict, conditions=[{'acl': 'public-read'}]
        )

    auth_cls.assert_called_with(
        credentials=base_signer_setup_s3v4['fixed_credentials'],
        region_name='region_name',
        service_name='signing_name',
    )
    assert presigned_url['url'] == 'https://s3.amazonaws.com/mybucket'
