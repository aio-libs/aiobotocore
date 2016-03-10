import asyncio
import pytest


@asyncio.coroutine
def fetch_all(pages):
    responses = []
    while True:
        n = yield from pages.next_page()
        if n is None:
            break
        responses.append(n)
    return responses


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
    assert len(bucket_contents) == 1
    assert bucket_contents[-1]['Key'] == 'a+b/foo'

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
    responses = yield from fetch_all(pages)

    assert len(responses) == 5, responses
    key_names = [el['Contents'][0]['Key'] for el in responses]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']


@pytest.mark.run_loop
def test_can_paginate_with_page_size(s3_client, bucket_name, create_object,
                                     loop):
    for i in range(5):
        key_name = 'key%s' % i
        yield from create_object(key_name)

    # Eventual consistency.
    yield from asyncio.sleep(3, loop=loop)
    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(PaginationConfig={'PageSize': 1},
                               Bucket=bucket_name)

    responses = yield from fetch_all(pages)
    assert len(responses) == 5, responses
    data = [r for r in responses]
    key_names = [el['Contents'][0]['Key'] for el in data]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']


@pytest.mark.xfail(raises=NotImplementedError)
@pytest.mark.run_loop
def test_result_key_iters(s3_client, bucket_name,):
    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(MaxKeys=2, Prefix='key/', Delimiter='/',
                               Bucket=bucket_name)
    iterators = pages.result_key_iters()
    assert iterators


@pytest.mark.run_loop
def test_can_get_and_put_object(s3_client, create_object, bucket_name, loop):
    yield from create_object('foobarbaz', body='body contents')
    # Eventual consistency.
    yield from asyncio.sleep(3, loop=loop)

    resp = yield from s3_client.get_object(Bucket=bucket_name, Key='foobarbaz')
    data = yield from resp['Body'].read()
    # TODO: think about better api and make behavior like in aiohttp
    resp['Body'].close()
    assert data == b'body contents'


@pytest.mark.run_loop
def test_get_object_stream_wrapper(s3_client, create_object, bucket_name):
    yield from create_object('foobarbaz', body='body contents')
    response = yield from s3_client.get_object(Bucket=bucket_name,
                                               Key='foobarbaz')
    body = response['Body']
    # TODO add set_socket_timeout function
    # Am able to set a socket timeout
    # body.set_socket_timeout(10)
    chunk1 = yield from body.read(1)
    chunk2 = yield from body.read()
    assert chunk1 == b'b'
    assert chunk2 == b'ody contents'


@pytest.mark.run_loop
def test_paginate_max_items(s3_client, create_multipart_upload, bucket_name,
                            loop):
    yield from create_multipart_upload('foo/key1')
    yield from create_multipart_upload('foo/key1')
    yield from create_multipart_upload('foo/key1')
    yield from create_multipart_upload('foo/key2')
    yield from create_multipart_upload('foobar/key1')
    yield from create_multipart_upload('foobar/key2')
    yield from create_multipart_upload('bar/key1')
    yield from create_multipart_upload('bar/key2')

    # Verify when we have MaxItems=None, we get back all 8 uploads.
    yield from pytest.aio.assert_num_uploads_found(
        s3_client, bucket_name, 'list_multipart_uploads', max_items=None,
        num_uploads=8, loop=loop)

    # Verify when we have MaxItems=1, we get back 1 upload.
    yield from pytest.aio.assert_num_uploads_found(
        s3_client, bucket_name, 'list_multipart_uploads', max_items=1,
        num_uploads=1, loop=loop)

    paginator = s3_client.get_paginator('list_multipart_uploads')
    # Works similar with build_full_result()
    pages = paginator.paginate(PaginationConfig={'MaxItems': 1},
                               Bucket=bucket_name)
    full_result = yield from pages.build_full_result()
    assert len(full_result['Uploads']) == 1


@pytest.mark.run_loop
def test_paginate_within_page_boundaries(s3_client, create_object,
                                         bucket_name):
    yield from create_object('a')
    yield from create_object('b')
    yield from create_object('c')
    yield from create_object('d')
    paginator = s3_client.get_paginator('list_objects')
    # First do it without a max keys so we're operating on a single page of
    # results.
    pages = paginator.paginate(PaginationConfig={'MaxItems': 1},
                               Bucket=bucket_name)
    first = yield from pages.build_full_result()
    t1 = first['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t1},
        Bucket=bucket_name)
    second = yield from pages.build_full_result()
    t2 = second['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t2},
        Bucket=bucket_name)
    third = yield from pages.build_full_result()
    t3 = third['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t3},
        Bucket=bucket_name)
    fourth = yield from pages.build_full_result()

    assert first['Contents'][-1]['Key'] == 'a'
    assert second['Contents'][-1]['Key'] == 'b'
    assert third['Contents'][-1]['Key'] == 'c'
    assert fourth['Contents'][-1]['Key'] == 'd'


@pytest.mark.run_loop
def test_unicode_key_put_list(s3_client, bucket_name, create_object):
    # Verify we can upload a key with a unicode char and list it as well.
    key_name = u'\u2713'
    yield from create_object(key_name)
    parsed = yield from s3_client.list_objects(Bucket=bucket_name)
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == key_name
    parsed = yield from s3_client.get_object(Bucket=bucket_name, Key=key_name)
    data = yield from parsed['Body'].read()
    assert data == b'foo'


@pytest.mark.run_loop
def test_unicode_system_character(s3_client, bucket_name, create_object):
    # Verify we can use a unicode system character which would normally
    # break the xml parser
    key_name = 'foo\x08'
    yield from create_object(key_name)
    parsed = yield from s3_client.list_objects(Bucket=bucket_name)
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == key_name

    parsed = yield from s3_client.list_objects(Bucket=bucket_name,
                                               EncodingType='url')
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == 'foo%08'


@pytest.mark.run_loop
def test_non_normalized_key_paths(s3_client, bucket_name, create_object):
    # The create_object method has assertEqual checks for 200 status.
    yield from create_object('key./././name')
    bucket = yield from s3_client.list_objects(Bucket=bucket_name)
    bucket_contents = bucket['Contents']
    assert len(bucket_contents) == 1
    assert bucket_contents[0]['Key'] == 'key./././name'


@pytest.mark.skipif(True, reason='Not supported')
@pytest.mark.run_loop
def test_reset_stream_on_redirects(region, create_bucket):
    # Create a bucket in a non classic region.
    bucket_name = yield from create_bucket(region)
    # Then try to put a file like object to this location.
    assert bucket_name


@pytest.mark.run_loop
def test_copy_with_quoted_char(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo'
    yield from create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    source = '%s/%s' % (bucket_name, key_name)
    yield from s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2, CopySource=source)

    # Now verify we can retrieve the copied object.
    resp = yield from s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = yield from resp['Body'].read()
    assert data == b'foo'


@pytest.mark.run_loop
def test_copy_with_query_string(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo?notVersionid=bar'
    yield from create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    yield from s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2,
        CopySource='%s/%s' % (bucket_name, key_name))

    # Now verify we can retrieve the copied object.
    resp = yield from s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = yield from resp['Body'].read()
    assert data == b'foo'


@pytest.mark.run_loop
def test_can_copy_with_dict_form(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo?versionId=abcd'
    yield from create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    yield from s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2,
        CopySource={'Bucket': bucket_name, 'Key': key_name})

    # Now verify we can retrieve the copied object.
    resp = yield from s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = yield from resp['Body'].read()
    assert data == b'foo'


@pytest.mark.run_loop
def test_copy_with_s3_metadata(s3_client, create_object, bucket_name):
    key_name = 'foo.txt'
    yield from create_object(key_name=key_name)
    copied_key = 'copied.txt'
    parsed = yield from s3_client.copy_object(
        Bucket=bucket_name, Key=copied_key,
        CopySource='%s/%s' % (bucket_name, key_name),
        MetadataDirective='REPLACE',
        Metadata={"mykey": "myvalue", "mykey2": "myvalue2"})
    pytest.aio.assert_status_code(parsed, 200)


@pytest.mark.parametrize('region', ['us-east-1'])
@pytest.mark.parametrize('signature_version', ['s3'])
@pytest.mark.run_loop
def test_presign_with_existing_query_string_values(s3_client, bucket_name,
                                                   aio_session, create_object):
    key_name = 'foo.txt'
    yield from create_object(key_name=key_name)
    content_disposition = 'attachment; filename=foo.txt;'
    params = {'Bucket': bucket_name,
              'Key': key_name,
              'ResponseContentDisposition': content_disposition}
    presigned_url = s3_client.generate_presigned_url(
        'get_object', Params=params)
    # Try to retrieve the object using the presigned url.

    resp = yield from aio_session.get(presigned_url)
    data = yield from resp.read()
    resp.close()
    assert resp.headers['Content-Disposition'] == content_disposition
    assert data == b'foo'


@pytest.mark.parametrize('region', ['us-east-1'])
@pytest.mark.parametrize('signature_version', ['s3v4'])
@pytest.mark.run_loop
def test_presign_sigv4(s3_client, bucket_name, aio_session, create_object):
    key = 'myobject'
    yield from create_object(key_name=key)
    presigned_url = s3_client.generate_presigned_url(
        'get_object', Params={'Bucket': bucket_name, 'Key': key})
    msg = "Host was suppose to be the us-east-1 endpoint, " \
          "instead got: %s" % presigned_url
    assert presigned_url.startswith('https://s3.amazonaws.com/%s/%s'
                                    % (bucket_name, key)), msg

    # Try to retrieve the object using the presigned url.
    resp = yield from aio_session.get(presigned_url)
    data = yield from resp.read()
    assert data == b'foo'
