import pytest


@pytest.mark.moto
@pytest.mark.parametrize('signature_version', ['v4'])
@pytest.mark.run_loop
def test_can_get_item(dynamodb_client, table_name, dynamodb_put_item):
    test_value = 'testValue'
    yield from dynamodb_put_item(test_value)
    response = yield from dynamodb_client.get_item(
        TableName=table_name,
        Key={
            'testKey': {
                'S': test_value
            }
        },
    )
    pytest.aio.assert_status_code(response, 200)
    assert response['Item']['testKey'] == {'S': test_value}
