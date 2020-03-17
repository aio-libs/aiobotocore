import pytest
import json
import mock
from aiobotocore import utils
from botocore.utils import MetadataRetrievalError


# From class TestContainerMetadataFetcher
def fake_aiohttp_session(response_body, response_status):

    class FakeAioHttpSession(object):
        class FakeResponse(object):
            def __init__(self, *args, **kwargs):
                self.status = response_status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def text(self):
                return response_body

            async def json(self):
                return json.loads(response_body)

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, *args, **kwargs):
            return self.FakeResponse()

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
    http = fake_aiohttp_session(json_body, 200)

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    resp = await fetcher.retrieve_uri('/foo?id=1')
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'

    resp = await fetcher.retrieve_full_uri('http://localhost/foo?id=1')
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containermetadatafetcher_retrieve_url_bad_status():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session(json_body, 500)

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containermetadatafetcher_retrieve_url_not_json():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session(json_body, 200)

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')
