# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

from aiobotocore.session import get_session


def get_items(start_num, num_items):
    """
    Generate a sequence of dynamo items

    :param start_num: Start index
    :type start_num: int
    :param num_items: Number of items
    :type num_items: int
    :return: List of dictionaries
    :rtype: list of dict
    """
    result = []
    for i in range(start_num, start_num + num_items):
        result.append({'pk': {'S': f'item{i}'}})
    return result


def create_batch_write_structure(table_name, start_num, num_items):
    """
    Create item structure for passing to batch_write_item

    :param table_name: DynamoDB table name
    :type table_name: str
    :param start_num: Start index
    :type start_num: int
    :param num_items: Number of items
    :type num_items: int
    :return: dictionary of tables to write to
    :rtype: dict
    """
    return {
        table_name: [
            {'PutRequest': {'Item': item}}
            for item in get_items(start_num, num_items)
        ]
    }


async def go():
    session = get_session()
    async with session.create_client(
        'dynamodb', region_name='us-west-2'
    ) as client:
        table_name = 'test'

        print('Writing to dynamo')
        start = 0
        while True:
            # Loop adding 25 items to dynamo at a time
            request_items = create_batch_write_structure(table_name, start, 25)
            response = await client.batch_write_item(
                RequestItems=request_items
            )
            if len(response['UnprocessedItems']) == 0:
                print('Wrote 25 items to dynamo')
            else:
                # Hit the provisioned write limit
                print('Hit write limit, backing off then retrying')
                await asyncio.sleep(5)

                # Items left over that haven't been inserted
                unprocessed_items = response['UnprocessedItems']
                print('Resubmitting items')
                # Loop until unprocessed items are written
                while len(unprocessed_items) > 0:
                    response = await client.batch_write_item(
                        RequestItems=unprocessed_items
                    )
                    # If any items are still left over, add them to the
                    # list to be written
                    unprocessed_items = response['UnprocessedItems']

                    # If there are items left over, we could do with
                    # sleeping some more
                    if len(unprocessed_items) > 0:
                        print('Backing off for 5 seconds')
                        await asyncio.sleep(5)

                # Inserted all the unprocessed items, exit loop
                print('Unprocessed items successfully inserted')
                break

            start += 25

        # See if DynamoDB has the last item we inserted
        final_item = 'item' + str(start + 24)
        print(f'Item "{final_item}" should exist')

        response = await client.get_item(
            TableName=table_name, Key={'pk': {'S': final_item}}
        )
        print(f'Response: {response["Item"]}')


if __name__ == '__main__':
    asyncio.run(go())
