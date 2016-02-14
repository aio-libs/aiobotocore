import asyncio
import pytest


@pytest.mark.run_loop
def test_can_make_request(s3_client):
    # Basic smoke test to ensure we can talk to s3.
    result = yield from s3_client.list_buckets()
    # Can't really assume anything about whether or not they have buckets,
    # but we can assume something about the structure of the response.
    actual_keys = sorted(list(result.keys()))
    assert actual_keys == ['Buckets', 'Owner', 'ResponseMetadata']


@pytest.mark.run_loop
def test_can_get_bucket_location(s3_client, bucket_name):
    result = yield from s3_client.get_bucket_location(Bucket=bucket_name)
    assert 'LocationConstraint' in result
    # For buckets in us-east-1 (US Classic Region) this will be None
    # TODO fix this
    assert result['LocationConstraint'] in [None, 'us-west-2']


@pytest.mark.run_loop
def test_can_delete_urlencoded_object(s3_client, bucket_name, create_object):
    key_name = 'a+b/foo'
    yield from create_object(key_name=key_name)
    resp = yield from s3_client.list_objects(Bucket=bucket_name)
    bucket_contents = resp['Contents']
    # assert len(bucket_contents) == 1
    assert len(bucket_contents) > 0
    # assert bucket_contents[-1]['Key'] == 'a+b/foo'

    resp = yield from s3_client.list_objects(Bucket=bucket_name, Prefix='a+b')
    subdir_contents = resp['Contents']
    assert len(subdir_contents) == 1
    assert subdir_contents[0]['Key'] == 'a+b/foo'

    response = yield from s3_client.delete_object(
        Bucket=bucket_name, Key=key_name)
    pytest.aio.assert_status_code(response, 204)


@pytest.mark.run_loop
def test_can_paginate(s3_client, bucket_name, create_object, loop):
    for i in range(5):
        key_name = 'key%s' % i
        yield from create_object(key_name)

    # Eventual consistency.
    yield from asyncio.sleep(3, loop=loop)
    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(MaxKeys=1, Bucket=bucket_name)

    responses = []
    while True:
        n = yield from pages.next_page()
        print(n)
        if n is None:
            break
        responses.append(n)

    assert len(responses) == 5, responses
    key_names = [el['Contents'][0]['Key'] for el in responses]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']
