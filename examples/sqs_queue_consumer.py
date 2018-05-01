#!/usr/bin/env python3
"""
aiobotocore SQS Consumer Example
"""
import asyncio
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

    print('Pulling messages off the queue')

    while True:
        try:
            # This loop wont spin really fast as there is
            # essentially a sleep in the receieve_message call
            response = await client.receive_message(
                QueueUrl=queue_url,
                WaitTimeSeconds=2,
            )

            if 'Messages' in response:
                for msg in response['Messages']:
                    print('Got msg "{0}"'.format(msg['Body']))
                    # Need to remove msg from queue or else it'll reappear
                    await client.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=msg['ReceiptHandle']
                    )
            else:
                print('No messages in queue')
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
