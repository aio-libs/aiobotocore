import botocore
import pytest

from aiobotocore.httpsession import AIOHTTPSession


async def test_cannot_create_client_sessions_outside_context(session):
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


async def test_ssl_context_built_off_loop_on_first_request(mocker):
    # Regression for #1469: the blocking SSL/file-I/O closure inside
    # _create_connector must run via asyncio.to_thread, not on the loop.
    to_thread = mocker.patch(
        'aiobotocore.httpsession.asyncio.to_thread',
        wraps=__import__('asyncio').to_thread,
    )
    async with AIOHTTPSession() as http:
        await http._get_session(proxy_url=None)
        # First call: SSL build dispatched to a thread.
        to_thread.assert_called_once()
        first_arg = to_thread.call_args.args[0]
        assert first_arg.__name__ == '_build_ssl_context'

        # Second call: cached connector, no additional thread dispatch.
        await http._get_session(proxy_url=None)
        to_thread.assert_called_once()
