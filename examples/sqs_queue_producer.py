#!/usr/bin/env python3
"""
aiobotocore SQS Producer Example
"""

# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio
import random

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

    print('Putting messages on the queue')

    msg_no = 1
    while True:
        try:
            msg_body = 'Message #{0}'.format(msg_no)
            response = yield from client.send_message(
                QueueUrl=queue_url,
                MessageBody=msg_body
            )
            msg_no += 1

            assert response['ResponseMetadata']['HTTPStatusCode'] == 200
            print('Pushed "{0}" to queue'.format(msg_body))

            yield from asyncio.sleep(random.randint(1, 4))
        except KeyboardInterrupt:
            break

    print('Finished')
    yield from client.close()


try:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(go(loop))
except KeyboardInterrupt:
    pass
