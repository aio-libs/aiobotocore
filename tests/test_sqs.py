import time

import pytest


@pytest.mark.moto
@pytest.mark.asyncio
async def test_list_queues(sqs_client, sqs_queue_url):
    response = await sqs_client.list_queues()
    pytest.aio.assert_status_code(response, 200)

    assert sqs_queue_url in response['QueueUrls']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_queue_name(sqs_client, sqs_queue_url):
    queue_name = sqs_queue_url.rsplit('/', 1)[-1]

    response = await sqs_client.get_queue_url(QueueName=queue_name)
    pytest.aio.assert_status_code(response, 200)

    assert sqs_queue_url == response['QueueUrl']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_put_pull_delete_test(sqs_client, sqs_queue_url):
    response = await sqs_client.send_message(
        QueueUrl=sqs_queue_url,
        MessageBody='test_message_1',
        MessageAttributes={
            'attr1': {'DataType': 'String', 'StringValue': 'value1'}
        },
    )
    pytest.aio.assert_status_code(response, 200)

    response = await sqs_client.receive_message(
        QueueUrl=sqs_queue_url, MessageAttributeNames=['attr1']
    )
    pytest.aio.assert_status_code(response, 200)

    # Messages wont be a key if its empty
    assert len(response.get('Messages', [])) == 1
    msg = response['Messages'][0]
    assert msg['Body'] == 'test_message_1'
    assert msg['MessageAttributes']['attr1']['StringValue'] == 'value1'

    receipt_handle = response['Messages'][0]['ReceiptHandle']
    response = await sqs_client.delete_message(
        QueueUrl=sqs_queue_url, ReceiptHandle=receipt_handle
    )
    pytest.aio.assert_status_code(response, 200)
    response = await sqs_client.receive_message(
        QueueUrl=sqs_queue_url,
    )
    pytest.aio.assert_status_code(response, 200)
    assert len(response.get('Messages', [])) == 0


@pytest.mark.moto
@pytest.mark.asyncio
async def test_put_pull_wait(sqs_client, sqs_queue_url):
    start = time.perf_counter()
    response = await sqs_client.receive_message(
        QueueUrl=sqs_queue_url, WaitTimeSeconds=2
    )
    end = time.perf_counter()
    pytest.aio.assert_status_code(response, 200)

    assert 'Messages' not in response
    assert end - start > 1.5
