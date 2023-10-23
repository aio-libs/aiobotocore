import logging

import pytest
from _pytest.logging import LogCaptureFixture

from aiobotocore import httpsession
from aiobotocore.config import AioConfig
from aiobotocore.session import AioSession


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_service_data(session):
    handler_called = False

    def handler(**kwargs):
        nonlocal handler_called
        handler_called = True

    session.register('service-data-loaded.s3', handler)
    await session.get_service_data('s3')

    assert handler_called


@pytest.mark.moto
@pytest.mark.asyncio
async def test_retry(
    session: AioSession, caplog: LogCaptureFixture, monkeypatch
):
    caplog.set_level(logging.DEBUG)

    config = AioConfig(
        connect_timeout=1,
        read_timeout=1,
        # this goes through a slightly different codepath than regular retries
        retries={
            "mode": "standard",
            "total_max_attempts": 3,
        },
    )

    async with session.create_client(
        's3',
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
        endpoint_url='http://localhost:7878',
    ) as client:
        # this needs the new style exceptions to work
        with pytest.raises(httpsession.EndpointConnectionError):
            await client.get_object(Bucket='foo', Key='bar')

        assert 'sleeping for' in caplog.text
