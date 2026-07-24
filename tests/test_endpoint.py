import pytest

from aiobotocore.config import AioConfig


async def test_invalid_endpoint_url(session, region, http_session_cls):
    endpoint_url = 'invalid_url'
    with pytest.raises(ValueError, match=f'Invalid endpoint: {endpoint_url}'):
        async with session.create_client(
            'ec2',
            region_name=region,
            endpoint_url=endpoint_url,
            config=AioConfig(http_session_cls=http_session_cls),
        ):
            # should not succeed in entering client context
            assert False  # pragma: no cover
