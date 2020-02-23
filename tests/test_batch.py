import pytest


@pytest.mark.moto
@pytest.mark.asyncio
async def test_batch(batch_client):
    job_queues = await batch_client.describe_job_queues()
    # AttributeError: 'AWSResponse' object has no attribute 'raw_headers'
