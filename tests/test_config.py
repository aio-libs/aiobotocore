import asyncio
from mock_server import AIOServer
from aiobotocore.session import AioSession
from aiobotocore.config import AioConfig
from botocore.config import Config
from botocore.exceptions import ParamValidationError
import pytest


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_connector_args():
    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(use_dns_cache=1)
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(keepalive_timeout="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(force_close="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(ssl_context="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # invalid key
        connector_args = dict(foo="1")
        AioConfig(connector_args)

    # test merge
    cfg = Config(read_timeout=75)
    aio_cfg = AioConfig({'keepalive_timeout': 75})
    aio_cfg.merge(cfg)

    assert cfg.read_timeout == 75
    assert aio_cfg.connector_args['keepalive_timeout'] == 75


@pytest.mark.moto
@pytest.mark.asyncio
async def test_connector_timeout(event_loop):
    session = AioSession(loop=event_loop)
    config = AioConfig(max_pool_connections=1, connect_timeout=1,
                       retries={'max_attempts': 0})
    async with AIOServer() as server, \
            session.create_client('s3', config=config,
                                  endpoint_url=server.endpoint_url,
                                  aws_secret_access_key='xxx',
                                  aws_access_key_id='xxx') as s3_client:

        async def get_and_wait():
            await s3_client.get_object(Bucket='foo', Key='bar')
            await asyncio.sleep(100)

        task1 = asyncio.Task(get_and_wait(), loop=event_loop)
        task2 = asyncio.Task(get_and_wait(), loop=event_loop)

        try:
            done, pending = await asyncio.wait([task1, task2],
                                               timeout=3, loop=event_loop)

            # second request should not timeout just because there isn't a
            # connector available
            assert len(pending) == 2
        finally:
            task1.cancel()
            task2.cancel()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_connector_timeout2(event_loop):
    session = AioSession(loop=event_loop)
    config = AioConfig(max_pool_connections=1, connect_timeout=1,
                       read_timeout=1, retries={'max_attempts': 0})
    async with AIOServer() as server, \
            session.create_client('s3', config=config,
                                  endpoint_url=server.endpoint_url,
                                  aws_secret_access_key='xxx',
                                  aws_access_key_id='xxx') as s3_client:

        with pytest.raises(asyncio.TimeoutError):
            resp = await s3_client.get_object(Bucket='foo', Key='bar')
            await resp["Body"].read()
