import pytest
import mock

import aiobotocore
import aiobotocore.credentials
import aiobotocore.signers
import botocore.auth
from botocore.model import ServiceId
from botocore.awsrequest import AWSRequest
from botocore.exceptions import UnknownClientMethodError, NoRegionError, \
    UnknownSignatureVersionError


# From class TestSigner
@pytest.fixture
async def base_signer_setup() -> dict:
    emitter = mock.AsyncMock()
    emitter.emit_until_response.return_value = (None, None)
    credentials = aiobotocore.credentials.AioCredentials('key', 'secret')

    signer = aiobotocore.signers.AioRequestSigner(ServiceId('service_name'),
                                                  'region_name', 'signing_name',
                                                  'v4', credentials, emitter)
    return {
        'credentials': credentials,
        'emitter': emitter,
        'signer': signer,
        'fixed_credentials': await credentials.get_frozen_credentials(),
        'request': AWSRequest()
    }


@pytest.mark.moto
@pytest.mark.asyncio
async def test_testsigner_get_auth(base_signer_setup: dict):
    auth_cls = mock.Mock()
    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'v4': auth_cls}):
        signer = base_signer_setup['signer']
        auth = await signer.get_auth('service_name', 'region_name')

        assert auth_cls.return_value is auth
        auth_cls.assert_called_with(
            credentials=base_signer_setup['fixed_credentials'],
            service_name='service_name',
            region_name='region_name'
        )


@pytest.mark.moto
@pytest.mark.asyncio
async def test_testsigner_region_required_for_sig4(base_signer_setup: dict):
    signer = aiobotocore.signers.AioRequestSigner(
        ServiceId('service_name'), None, 'signing_name',
        'v4', base_signer_setup['credentials'], base_signer_setup['emitter'])

    with pytest.raises(NoRegionError):
        await signer.sign('operation_name', base_signer_setup['request'])


@pytest.mark.moto
@pytest.mark.asyncio
async def test_testsigner_custom_sign_version(base_signer_setup: dict):
    signer = base_signer_setup['signer']
    with pytest.raises(UnknownSignatureVersionError):
        await signer.get_auth('service_name', 'region_name',
                              signature_version='bad')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_testsigner_choose_signer_override(base_signer_setup: dict):
    auth_cls = mock.Mock()
    auth_cls.REQUIRES_REGION = False
    base_signer_setup['emitter'].emit_until_response.return_value = (None, 'custom')

    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'custom': auth_cls}):
        signer = base_signer_setup['signer']
        request = base_signer_setup['request']
        await signer.sign('operation_name', request)

        fixed_credentials = base_signer_setup['fixed_credentials']
        auth_cls.assert_called_with(credentials=fixed_credentials)
        auth_cls.return_value.add_auth.assert_called_with(request)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_testsigner_generate_presigned_url(base_signer_setup: dict):
    auth_cls = mock.Mock()
    auth_cls.REQUIRES_REGION = True

    request_dict = {
        'headers': {},
        'url': 'https://foo.com',
        'body': b'',
        'url_path': '/',
        'method': 'GET',
        'context': {}
    }

    with mock.patch.dict(botocore.auth.AUTH_TYPE_MAPS, {'v4-query': auth_cls}):
        signer = base_signer_setup['signer']
        presigned_url = await signer.generate_presigned_url(
            request_dict, operation_name='operation_name'
        )

    auth_cls.assert_called_with(
        credentials=base_signer_setup['fixed_credentials'],
        region_name='region_name', service_name='signing_name',
        expires=3600
    )
    assert presigned_url == 'https://foo.com'


# From class TestGenerateUrl
@pytest.mark.moto
@pytest.mark.asyncio
async def test_signers_generate_presigned_urls():
    with mock.patch('aiobotocore.signers.AioRequestSigner.generate_presigned_url') \
            as cls_gen_presigned_url_mock:
        session = aiobotocore.session.get_session()
        async with session.create_client('s3', region_name='us-east-1',
                                         aws_access_key_id='lalala',
                                         aws_secret_access_key='lalala',
                                         aws_session_token='lalala') as client:

            # Uses HEAD as it covers more lines :)
            await client.generate_presigned_url('get_object',
                                                Params={'Bucket': 'mybucket',
                                                        'Key': 'mykey'},
                                                HttpMethod='HEAD')

            ref_request_dict = {
                'body': b'',
                'url': 'https://s3.amazonaws.com/mybucket/mykey',
                'headers': {},
                'query_string': {},
                'url_path': '/mybucket/mykey',
                'method': 'HEAD',
                'context': mock.ANY
            }

            cls_gen_presigned_url_mock.assert_called_with(
                request_dict=ref_request_dict,
                expires_in=3600,
                operation_name='GetObject')

            cls_gen_presigned_url_mock.reset_mock()

            with pytest.raises(UnknownClientMethodError):
                await client.generate_presigned_url('lalala')
