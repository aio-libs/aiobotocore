# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

import aiobotocore


async def go(loop):
    session = aiobotocore.get_session(loop=loop)
    client = session.create_client('sqs', region_name='us-west-2')

    print('Creating test_queue1')
    response = await client.create_queue(QueueName='test_queue1')
    queue_url = response['QueueUrl']

    response = await client.list_queues()

    print('Queue URLs:')
    for queue_name in response.get('QueueUrls', []):
        print(' ' + queue_name)

    print('Deleting queue {0}'.format(queue_url))
    await client.delete_queue(QueueUrl=queue_url)

    print('Done')
    await client.close()


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(go(loop))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
