async def test_batch(batch_client):
    job_queues = await batch_client.describe_job_queues()
    assert job_queues['jobQueues'] == []
