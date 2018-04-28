#!/usr/bin/env python3
"""
aiobotocore SQS Producer Example
"""
import asyncio
import random
import sys

import aiobotocore
import botocore.exceptions

QUEUE_NAME = 'test_queue12'


async def go(loop):
    # Boto should get credentials from ~/.aws/credentials or the environment
    session = aiobotocore.get_session(loop=loop)
    client = session.create_client('sqs', region_name='us-west-2')
    try:
        response = await client.get_queue_url(QueueName=QUEUE_NAME)
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] == \
                'AWS.SimpleQueueService.NonExistentQueue':
            print("Queue {0} does not exist".format(QUEUE_NAME))
            await client.close()
            sys.exit(1)
        else:
            raise

    queue_url = response['QueueUrl']

    print('Putting messages on the queue')

    msg_no = 1
    while True:
        try:
            msg_body = 'Message #{0}'.format(msg_no)
            await client.send_message(
                QueueUrl=queue_url,
                MessageBody=msg_body
            )
            msg_no += 1

            print('Pushed "{0}" to queue'.format(msg_body))

            await asyncio.sleep(random.randint(1, 4))
        except KeyboardInterrupt:
            break

    print('Finished')
    await client.close()


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(go(loop))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
