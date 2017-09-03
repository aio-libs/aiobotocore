#!/usr/bin/env python3
"""
aiobotocore SQS Consumer Example
"""

# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

import aiobotocore

QUEUE_NAME = 'test_queue1'


@asyncio.coroutine
def go(loop):
    session = aiobotocore.get_session(loop=loop)
    client = session.create_client('sqs', region_name='us-west-2')
    response = yield from client.get_queue_url(QueueName=QUEUE_NAME)
    assert response['ResponseMetadata']['HTTPStatusCode'] == 200
    assert 'QueueUrl' in response
    queue_url = response['QueueUrl']

    print('Pulling messages off the queue')

    while True:
        try:
            # This loop wont spin really fast as there is
            # essentially a sleep in the receieve_message call
            response = yield from client.receive_message(
                QueueUrl=queue_url,
                WaitTimeSeconds=2,
            )
            resp_status = response['ResponseMetadata']['HTTPStatusCode']
            assert resp_status == 200

            if 'Messages' in response:
                for msg in response['Messages']:
                    print('Got msg "{0}"'.format(msg['Body']))
                    # Need to remove msg from queue or else it'll reappear
                    yield from client.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=msg['ReceiptHandle']
                    )
            else:
                print('No messages in queue')
        except KeyboardInterrupt:
            break

    print('Finished')
    yield from client.close()


try:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(go(loop))
except KeyboardInterrupt:
    pass
