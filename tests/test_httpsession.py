import aiohttp
import anyio.to_thread
import botocore
import pytest
from botocore.awsrequest import AWSPreparedRequest

from aiobotocore._httpx import httpx
from aiobotocore.config import AioConfig
from aiobotocore.httpsession import AIOHTTPSession
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import AioSession


async def test_cannot_create_client_sessions_outside_context():
    session = AioSession()
    s3_client_context = session.create_client(
        's3',
        'us-west-2',
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    )

    async with s3_client_context as s3_client:
        pass

    with pytest.raises(
        botocore.exceptions.HTTPClientError,
        match="'NoneType' object has no attribute 'get'",
    ):
        await s3_client.list_buckets()


async def test_ssl_context_built_off_loop_on_first_request(
    mocker, current_http_backend
):
    # Regression for #1469: _build_ssl_context (which calls blocking SSL/file
    # APIs) must run off the event loop. aiohttp dispatches it with
    # asyncio.to_thread; httpx, which also runs on trio, uses anyio.
    if current_http_backend == 'httpx':
        run_sync = mocker.patch(
            'aiobotocore.httpxsession.anyio.to_thread.run_sync',
            wraps=anyio.to_thread.run_sync,
        )
        async with HttpxSession():
            # The httpx session builds its SSL context(s) on entry.
            run_sync.assert_called_once()
            assert run_sync.call_args.args[0].__name__ == '_build_ssl_contexts'
        return

    to_thread = mocker.patch(
        'aiobotocore.httpsession.asyncio.to_thread',
        wraps=__import__('asyncio').to_thread,
    )
    async with AIOHTTPSession() as http:
        await http._get_session(proxy_url=None)
        # First call: SSL build dispatched to a thread.
        to_thread.assert_called_once()
        first_arg = to_thread.call_args.args[0]
        assert first_arg.__name__ == '_build_ssl_contexts'

        # Second call: cached connector, no additional thread dispatch.
        await http._get_session(proxy_url=None)
        to_thread.assert_called_once()


async def test_ssl_context_built_inline_without_file_io(
    mocker, current_http_backend
):
    if current_http_backend == 'httpx':
        run_sync = mocker.patch(
            'aiobotocore.httpxsession.anyio.to_thread.run_sync',
            wraps=anyio.to_thread.run_sync,
        )
        async with HttpxSession(verify=False):
            run_sync.assert_not_called()
        return

    to_thread = mocker.patch(
        'aiobotocore.httpsession.asyncio.to_thread',
        wraps=__import__('asyncio').to_thread,
    )
    async with AIOHTTPSession(verify=False) as http:
        await http._get_session(proxy_url=None)
        to_thread.assert_not_called()


def _prepared_request(context=None):
    return AWSPreparedRequest(
        'GET', 'http://localhost:1/', {}, None, False, context=context
    )


def _spy_on_transport(mocker, current_http_backend):
    if current_http_backend == 'httpx':
        return mocker.patch.object(
            httpx.AsyncClient, 'send', side_effect=RuntimeError('stop')
        )
    return mocker.patch.object(
        aiohttp.ClientSession, 'request', side_effect=RuntimeError('stop')
    )


async def test_read_timeout_override_from_request_context(
    mocker, http_session_cls, current_http_backend
):
    spy = _spy_on_transport(mocker, current_http_backend)

    async with http_session_cls(timeout=(3, 7)) as http:
        with pytest.raises(botocore.exceptions.HTTPClientError):
            await http.send(_prepared_request({'read_timeout': 1.5}))

    if current_http_backend == 'httpx':
        timeout = spy.call_args.args[0].extensions['timeout']
        assert (timeout['connect'], timeout['read']) == (3, 1.5)
    else:
        timeout = spy.call_args.kwargs['timeout']
        assert (timeout.sock_connect, timeout.sock_read) == (3, 1.5)


async def test_read_timeout_falls_back_to_client_config(
    mocker, http_session_cls, current_http_backend
):
    spy = _spy_on_transport(mocker, current_http_backend)

    async with http_session_cls(timeout=(3, 7)) as http:
        with pytest.raises(botocore.exceptions.HTTPClientError):
            await http.send(_prepared_request())

    if current_http_backend == 'httpx':
        timeout = spy.call_args.args[0].extensions['timeout']
        assert (timeout['connect'], timeout['read']) == (3, 7)
    else:
        assert 'timeout' not in spy.call_args.kwargs


async def test_read_timeout_override_ignored_without_request_context(
    http_session_cls,
):
    # AWSPreparedRequest.context is a recent botocore addition.
    async with http_session_cls() as http:
        assert http._get_request_timeout(object()) is None


async def test_read_timeout_override_from_request_created_handler(
    mocker, http_session_cls, current_http_backend
):
    spy = _spy_on_transport(mocker, current_http_backend)
    config = AioConfig(
        http_session_cls=http_session_cls,
        connect_timeout=3,
        read_timeout=7,
        retries={'max_attempts': 0},
    )
    session = AioSession()
    async with session.create_client(
        's3',
        'us-east-1',
        aws_access_key_id='xxx',
        aws_secret_access_key='xxx',
        config=config,
    ) as s3_client:
        s3_client.meta.events.register(
            'request-created.s3.ListBuckets',
            lambda request, **kwargs: request.context.update(read_timeout=1.5),
        )
        with pytest.raises(botocore.exceptions.HTTPClientError):
            await s3_client.list_buckets()

    if current_http_backend == 'httpx':
        timeout = spy.call_args.args[0].extensions['timeout']
        assert (timeout['connect'], timeout['read']) == (3, 1.5)
    else:
        timeout = spy.call_args.kwargs['timeout']
        assert (timeout.sock_connect, timeout.sock_read) == (3, 1.5)
