import asyncio
import pytest
import json
import itertools
from typing import Union, List, Tuple

from botocore.exceptions import ReadTimeoutError
from botocore.utils import BadIMDSRequestError

from aiobotocore import utils
from aiobotocore._helpers import asynccontextmanager


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
        @asynccontextmanager
        async def acquire(self):
            yield self

        class FakeResponse(object):
            def __init__(self, request, *args, **kwargs):
                self.request = request
                self.url = request.url
                self._body, self.status_code = next(data)
                self.content = self._content()
                self.text = self._text()
                if not isinstance(self._body, str):
                    raise self._body

            async def _content(self):
                return self._body.encode('utf-8')

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def _text(self):
                return self._body

            async def json(self):
                return json.loads(self._body)

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def send(self, request):
            return self.FakeResponse(request)

    return FakeAioHttpSession()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_disabled():
    env = {'AWS_EC2_METADATA_DISABLED': 'true'}
    fetcher = utils.AioIMDSFetcher(env=env)

    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_get_token_success():
    session = fake_aiohttp_session([
        ('blah', 200),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=2,
                                   session=session,
                                   user_agent='test')
    response = await fetcher._fetch_metadata_token()
    assert response == 'blah'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_get_token_not_found():
    session = fake_aiohttp_session([
        ('blah', 404),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=2,
                                   session=session,
                                   user_agent='test')
    response = await fetcher._fetch_metadata_token()
    assert response is None


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_get_token_bad_request():
    session = fake_aiohttp_session([
        ('blah', 400),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=2,
                                   session=session,
                                   user_agent='test')
    with pytest.raises(BadIMDSRequestError):
        await fetcher._fetch_metadata_token()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_get_token_timeout():
    session = fake_aiohttp_session([
        (ReadTimeoutError(endpoint_url='aaa'), 500),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=2,
                                   session=session)

    response = await fetcher._fetch_metadata_token()
    assert response is None


@pytest.mark.moto
@pytest.mark.asyncio
async def test_idmsfetcher_get_token_retry():
    session = fake_aiohttp_session([
        ('blah', 500),
        ('blah', 500),
        ('token', 200),
    ])

    fetcher = utils.AioIMDSFetcher(num_attempts=3,
                                   session=session)

    response = await fetcher._fetch_metadata_token()
    assert response == 'token'


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
    response = await fetcher._get_request('path', None, 'some_token')

    assert await response.text == 'data'

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
