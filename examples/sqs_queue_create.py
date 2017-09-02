# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

import aiobotocore


async def go(loop):
    session = aiobotocore.get_session(loop=loop)
    async with session.create_client('sqs', region_name='us-west-2') as client:
        print('Creating test_queue1')
        response = await client.create_queue(QueueName='test_queue1')
        assert response['ResponseMetadata']['HTTPStatusCode'] == 200
        assert 'QueueUrl' in response
        queue_url = response['QueueUrl']

        response = await client.list_queues()
        assert response['ResponseMetadata']['HTTPStatusCode'] == 200

        print('Queue Names:')
        for queue_name in response.get('QueueUrls', []):
            print(' ' + queue_name)

        print('Deleting queue {0}'.format(queue_url))
        response = await client.delete_queue(QueueUrl=queue_url)
        assert response['ResponseMetadata']['HTTPStatusCode'] == 200

        print('Done')

loop = asyncio.get_event_loop()
loop.run_until_complete(go(loop))
