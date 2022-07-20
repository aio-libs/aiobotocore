# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio
import uuid

from aiobotocore.session import get_session


async def go():
    session = get_session()
    async with session.create_client(
        'dynamodb', region_name='us-west-2'
    ) as client:
        # Create random table name
        table_name = f'aiobotocore-{uuid.uuid4()}'

        print('Requesting table creation...')
        await client.create_table(
            TableName=table_name,
            AttributeDefinitions=[
                {'AttributeName': 'testKey', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'testKey', 'KeyType': 'HASH'},
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10,
            },
        )

        print("Waiting for table to be created...")
        waiter = client.get_waiter('table_exists')
        await waiter.wait(TableName=table_name)
        print(f"Table {table_name} created")


if __name__ == '__main__':
    asyncio.run(go())
