import asyncio
import pytest
import json
import mock
import itertools
from typing import Union, List, Tuple

from aiobotocore import utils
from botocore.utils import MetadataRetrievalError


# From class TestContainerMetadataFetcher
def fake_aiohttp_session(responses: Union[List[Tuple[Union[str, object], int]],
                                          Tuple[Union[str, object], int]]):
    """
    Dodgy shim class
    """
    if isinstance(responses, Tuple):
        data = itertools.cycle([responses])
    else:
        data = iter(responses)

    class FakeAioHttpSession(object):
        class FakeResponse(object):
            def __init__(self, url, *args, **kwargs):
                self.url = url
                self._body, self.status = next(data)
                if not isinstance(self._body, str):
                    raise self._body

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def text(self):
                return self._body

            async def json(self):
                return json.loads(self._body)

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, *args, **kwargs):
            return self.FakeResponse(url)

    return FakeAioHttpSession


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containermetadatafetcher_retrieve_url():
    json_body = json.dumps({
        "AccessKeyId": "a",
        "SecretAccessKey": "b",
        "Token": "c",
        "Expiration": "d"
    })

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 200))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    resp = await fetcher.retrieve_uri('/foo?id=1')
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'

    resp = await fetcher.retrieve_full_uri('http://localhost/foo?id=1',
                                           {'extra': 'header'})
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containermetadatafetcher_retrieve_url_bad_status():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 500))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containermetadatafetcher_retrieve_url_not_json():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 200))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_instancemetadatafetcher_retrieve_creds():
    with mock.patch('aiobotocore.utils.AioInstance'
                    'MetadataFetcher._get_request') as mock_obj:
        mock_obj.side_effect = [
            utils.AioIMDSFetcher.Response(200, 'some-role',
                                          'someurl'),
            utils.AioIMDSFetcher.Response(200, '{"AccessKeyId": "foo", '
                                               '"SecretAccessKey": "bar", '
                                               '"Token": "baz", '
                                               '"Expiration": "bah"}',
                                          'someurl'),
        ]

        fetcher = utils.AioInstanceMetadataFetcher()

        creds = await fetcher.retrieve_iam_role_credentials()
        assert creds['role_name'] == 'some-role'
        assert creds['access_key'] == 'foo'
        assert creds['secret_key'] == 'bar'
        assert creds['token'] == 'baz'
        assert creds['expiry_time'] == 'bah'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_instancemetadatafetcher_partial_response():
    with mock.patch('aiobotocore.utils.AioInstance'
                    'MetadataFetcher._get_request') as mock_obj:
        mock_obj.side_effect = [
            utils.AioIMDSFetcher.Response(200, 'some-role',
                                          'someurl'),
            utils.AioIMDSFetcher.Response(200, '{"AccessKeyId": "foo"}',
                                          'someurl'),
        ]

        fetcher = utils.AioInstanceMetadataFetcher()

        creds = await fetcher.retrieve_iam_role_credentials()
        assert creds == {}


@pytest.mark.moto
@pytest.mark.asyncio
async def test_instancemetadatafetcher_max_retries():
    with mock.patch('aiobotocore.utils.AioInstance'
                    'MetadataFetcher._get_request') as mock_obj:
        mock_obj.side_effect = utils.AioInstanceMetadataFetcher.\
            _RETRIES_EXCEEDED_ERROR_CLS()

        fetcher = utils.AioInstanceMetadataFetcher()

        creds = await fetcher.retrieve_iam_role_credentials()
        assert creds == {}


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_disabled():
    env = {'AWS_EC2_METADATA_DISABLED': 'true'}
    fetcher = utils.AioIMDSFetcher(env=env)

    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_retry():
    session = fake_aiohttp_session([
        ('blah', 500),
        ('data', 200),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=2,
                                   session=session,
                                   user_agent='test')
    response = await fetcher._get_request('path', None)

    assert response.text == 'data'

    session = fake_aiohttp_session([
        ('blah', 500),
        ('data', 200),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=1, session=session)
    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_timeout():
    session = fake_aiohttp_session([
        (asyncio.TimeoutError(), 500),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=1,
                                   session=session)

    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)
