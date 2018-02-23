import asyncio
from mock_server import AIOServer
from aiobotocore import get_session
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
@pytest.mark.run_loop
def test_connector_timeout(loop):
    server = AIOServer(9999)
    session = get_session(loop=loop)
    config = AioConfig(max_pool_connections=1, connect_timeout=1,
                       retries={'max_attempts': 0})
    s3_client = session.create_client('s3', config=config,
                                      endpoint_url=server.endpoint_url)

    try:
        server.wait_until_up()

        @asyncio.coroutine
        def get_and_wait():
            yield from s3_client.get_object(Bucket='foo', Key='bar')
            yield from asyncio.sleep(100)

        # this should not raise as we won't have any issues connecting to the
        task1 = asyncio.Task(get_and_wait())
        task2 = asyncio.Task(get_and_wait())

        try:
            done, pending = yield from asyncio.wait([task1, task2], timeout=3)

            # second request should not timeout just because there isn't a
            # connector available
            assert len(pending) == 2
        finally:
            task1.cancel()
            task2.cancel()
    finally:
        s3_client.close()
        yield from server.stop()
