import pytest

from aiobotocore.session import AioSession


@pytest.mark.moto
@pytest.mark.asyncio
async def test_monitor_response_received(session: AioSession, s3_client):
    # Basic smoke test to ensure we can talk to s3.
    handler_kwargs = {}

    def handler(**kwargs):
        nonlocal handler_kwargs
        handler_kwargs = kwargs

    s3_client.meta.events.register('response-received.s3.ListBuckets', handler)
    result = await s3_client.list_buckets()
    # Can't really assume anything about whether or not they have buckets,
    # but we can assume something about the structure of the response.
    actual_keys = sorted(list(result.keys()))
    assert actual_keys == ['Buckets', 'Owner', 'ResponseMetadata']

    assert handler_kwargs['response_dict']['status_code'] == 200
