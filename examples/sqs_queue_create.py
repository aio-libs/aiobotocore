# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

from aiobotocore.session import get_session


async def go():
    session = get_session()
    async with session.create_client('sqs', region_name='us-west-2') as client:
        print('Creating test_queue1')
        response = await client.create_queue(QueueName='test_queue1')
        queue_url = response['QueueUrl']

        response = await client.list_queues()

        print('Queue URLs:')
        for queue_name in response.get('QueueUrls', []):
            print(f' {queue_name}')

        print(f'Deleting queue {queue_url}')
        await client.delete_queue(QueueUrl=queue_url)

        print('Done')


if __name__ == '__main__':
    asyncio.run(go())
