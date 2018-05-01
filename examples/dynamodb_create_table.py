# Boto should get credentials from ~/.aws/credentials or the environment
import uuid
import asyncio

import aiobotocore


async def go(loop):
    session = aiobotocore.get_session(loop=loop)
    client = session.create_client('dynamodb', region_name='us-west-2')
    # Create random table name
    table_name = 'aiobotocore-' + str(uuid.uuid4())

    print('Requesting table creation...')
    await client.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {
                'AttributeName': 'testKey',
                'AttributeType': 'S'
            },
        ],
        KeySchema=[
            {
                'AttributeName': 'testKey',
                'KeyType': 'HASH'
            },
        ],
        ProvisionedThroughput={
            'ReadCapacityUnits': 10,
            'WriteCapacityUnits': 10
        }
    )

    print("Waiting for table to be created...")
    waiter = client.get_waiter('table_exists')
    await waiter.wait(TableName=table_name)
    print("Table {0} created".format(table_name))

    await client.close()


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(go(loop))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
