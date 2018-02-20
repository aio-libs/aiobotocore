import asyncio
import uuid
import pytest

from aiobotocore.waiter import WaiterError


@pytest.fixture
def dynamodb_table_def():
    table_name = str(uuid.uuid4())
    return dict(
        TableName=table_name,
        AttributeDefinitions=[
            {
                'AttributeName': 'testKey',
                'AttributeType': 'N'
            },
        ],
        KeySchema=[
            {
                'AttributeName': 'testKey',
                'KeyType': 'HASH'
            },
        ],
        ProvisionedThroughput={
            'ReadCapacityUnits': 1,
            'WriteCapacityUnits': 1
        }
    )


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_get_item(dynamodb_client, table_name, dynamodb_put_item):
    test_value = 'testValue'
    yield from dynamodb_put_item(test_value)
    response = yield from dynamodb_client.get_item(
        TableName=table_name,
        Key={
            'testKey': {
                'S': test_value
            }
        }
    )
    pytest.aio.assert_status_code(response, 200)
    assert response['Item']['testKey'] == {'S': test_value}


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_create_waiter(dynamodb_client, dynamodb_table_def):
    table_name = dynamodb_table_def['TableName']

    response = yield from dynamodb_client.create_table(**dynamodb_table_def)

    pytest.aio.assert_status_code(response, 200)

    waiter = dynamodb_client.get_waiter('table_exists')
    yield from waiter.wait(TableName=table_name)

    response = yield from dynamodb_client.describe_table(
        TableName=table_name
    )
    assert response['Table']['TableStatus'] == 'ACTIVE'


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_batch_write_scan(dynamodb_client, table_name):
    response = yield from dynamodb_client.batch_write_item(
        RequestItems={
            table_name: [
                {
                    'PutRequest': {
                        'Item': {
                            'testKey': {'S': 'key1'},
                            'testKey2': {'S': 'key2'},
                        }
                    }
                },
                {
                    'PutRequest': {
                        'Item': {
                            'testKey': {'S': 'key3'},
                            'testKey2': {'S': 'key4'},
                        }
                    }
                }
            ]
        }
    )
    pytest.aio.assert_status_code(response, 200)

    response = yield from dynamodb_client.scan(TableName=table_name)
    test_keys = sorted([item['testKey']['S'] for item in response['Items']])

    assert response['Count'] == 2
    assert test_keys == ['key1', 'key3']


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_delete_table(dynamodb_client, dynamodb_table_def):
    table_name = dynamodb_table_def['TableName']

    yield from dynamodb_client.create_table(**dynamodb_table_def)

    response = yield from dynamodb_client.describe_table(
        TableName=table_name
    )
    assert response['Table']['TableStatus'] == 'ACTIVE'

    response = yield from dynamodb_client.delete_table(
        TableName=table_name
    )
    pytest.aio.assert_status_code(response, 200)

    response = yield from dynamodb_client.list_tables()
    assert table_name not in response['TableNames']


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_waiter_table_exists_failure(dynamodb_client):
    waiter = dynamodb_client.get_waiter('table_exists')
    with pytest.raises(
            WaiterError,
            match='Waiter TableExists failed: Max attempts exceeded'):
        yield from waiter.wait(
            TableName='unknown',
            WaiterConfig=dict(Delay=1, MaxAttempts=1))
