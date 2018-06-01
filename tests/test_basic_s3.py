import pytest
import aiohttp
import asyncio


async def fetch_all(pages):
    responses = []
    while True:
        n = await pages.next_page()
        if n is None:
            break
        responses.append(n)
    return responses


@pytest.mark.moto
@pytest.mark.asyncio
async def test_can_make_request(s3_client):
    # Basic smoke test to ensure we can talk to s3.
    result = await s3_client.list_buckets()
    # Can't really assume anything about whether or not they have buckets,
    # but we can assume something about the structure of the response.
    actual_keys = sorted(list(result.keys()))
    assert actual_keys == ['Buckets', 'Owner', 'ResponseMetadata']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_fail_proxy_request(aa_fail_proxy_config, s3_client):
    # based on test_can_make_request

    with pytest.raises(aiohttp.ClientConnectorError):
        await s3_client.list_buckets()


@pytest.mark.asyncio
@pytest.mark.parametrize('mocking_test', [False])
async def test_succeed_proxy_request(aa_succeed_proxy_config, s3_client):
    result = await s3_client.list_buckets()
    actual_keys = sorted(list(result.keys()))
    assert actual_keys == ['Buckets', 'Owner', 'ResponseMetadata']


@pytest.mark.asyncio
@pytest.mark.moto
async def test_can_get_bucket_location(s3_client, bucket_name):
    result = await s3_client.get_bucket_location(Bucket=bucket_name)
    assert 'LocationConstraint' in result
    # For buckets in us-east-1 (US Classic Region) this will be None
    # TODO fix this
    assert result['LocationConstraint'] in [None, 'us-west-2', 'us-east-1']


@pytest.mark.moto
@pytest.mark.asyncio
async def test_can_delete_urlencoded_object(s3_client, bucket_name,
                                            create_object):
    key_name = 'a+b/foo'
    await create_object(key_name=key_name)
    resp = await s3_client.list_objects(Bucket=bucket_name)
    bucket_contents = resp['Contents']
    assert len(bucket_contents) == 1
    assert bucket_contents[-1]['Key'] == 'a+b/foo'

    resp = await s3_client.list_objects(Bucket=bucket_name, Prefix='a+b')
    subdir_contents = resp['Contents']
    assert len(subdir_contents) == 1
    assert subdir_contents[0]['Key'] == 'a+b/foo'

    response = await s3_client.delete_object(
        Bucket=bucket_name, Key=key_name)
    pytest.aio.assert_status_code(response, 204)


@pytest.mark.asyncio
@pytest.mark.moto
async def test_can_paginate(s3_client, bucket_name, create_object):
    for i in range(5):
        key_name = 'key%s' % i
        await create_object(key_name)

    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(MaxKeys=1, Bucket=bucket_name)
    responses = await fetch_all(pages)

    assert len(responses) == 5, responses
    key_names = [el['Contents'][0]['Key'] for el in responses]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']


@pytest.mark.asyncio
@pytest.mark.moto
async def test_can_paginate_with_page_size(
        s3_client, bucket_name, create_object):
    for i in range(5):
        key_name = 'key%s' % i
        await create_object(key_name)

    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(PaginationConfig={'PageSize': 1},
                               Bucket=bucket_name)

    responses = await fetch_all(pages)
    assert len(responses) == 5, responses
    data = [r for r in responses]
    key_names = [el['Contents'][0]['Key'] for el in data]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']


@pytest.mark.asyncio
@pytest.mark.moto
async def test_can_paginate_iterator(s3_client, bucket_name, create_object):
    for i in range(5):
        key_name = 'key%s' % i
        await create_object(key_name)

    paginator = s3_client.get_paginator('list_objects')
    responses = []
    async for page in paginator.paginate(
            PaginationConfig={'PageSize': 1}, Bucket=bucket_name):
        assert not asyncio.iscoroutine(page)
        responses.append(page)
    assert len(responses) == 5, responses
    data = [r for r in responses]
    key_names = [el['Contents'][0]['Key'] for el in data]
    assert key_names == ['key0', 'key1', 'key2', 'key3', 'key4']


@pytest.mark.xfail(raises=NotImplementedError)
@pytest.mark.asyncio
async def test_result_key_iters(s3_client, bucket_name,):
    paginator = s3_client.get_paginator('list_objects')
    pages = paginator.paginate(MaxKeys=2, Prefix='key/', Delimiter='/',
                               Bucket=bucket_name)
    iterators = pages.result_key_iters()
    assert iterators


@pytest.mark.moto
@pytest.mark.asyncio
async def test_can_get_and_put_object(s3_client, create_object, bucket_name):
    await create_object('foobarbaz', body='body contents')
    resp = await s3_client.get_object(Bucket=bucket_name, Key='foobarbaz')
    data = await resp['Body'].read()
    # TODO: think about better api and make behavior like in aiohttp
    resp['Body'].close()
    assert data == b'body contents'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_object_stream_wrapper(s3_client, create_object,
                                         bucket_name):
    await create_object('foobarbaz', body='body contents')
    response = await s3_client.get_object(Bucket=bucket_name, Key='foobarbaz')
    body = response['Body']
    chunk1 = await body.read(1)
    chunk2 = await body.read()
    assert chunk1 == b'b'
    assert chunk2 == b'ody contents'
    response['Body'].close()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_object_stream_context(s3_client, create_object,
                                         bucket_name):
    await create_object('foobarbaz', body='body contents')
    response = await s3_client.get_object(Bucket=bucket_name, Key='foobarbaz')
    async with response['Body'] as stream:
        await stream.read()


@pytest.mark.asyncio
@pytest.mark.moto
async def test_paginate_max_items(
        s3_client, create_multipart_upload, bucket_name, event_loop):
    await create_multipart_upload('foo/key1')
    await create_multipart_upload('foo/key1')
    await create_multipart_upload('foo/key1')
    await create_multipart_upload('foo/key2')
    await create_multipart_upload('foobar/key1')
    await create_multipart_upload('foobar/key2')
    await create_multipart_upload('bar/key1')
    await create_multipart_upload('bar/key2')

    # Verify when we have MaxItems=None, we get back all 8 uploads.
    await pytest.aio.assert_num_uploads_found(
        s3_client, bucket_name, 'list_multipart_uploads', max_items=None,
        num_uploads=8, event_loop=event_loop)

    # Verify when we have MaxItems=1, we get back 1 upload.
    await pytest.aio.assert_num_uploads_found(
        s3_client, bucket_name, 'list_multipart_uploads', max_items=1,
        num_uploads=1, event_loop=event_loop)

    paginator = s3_client.get_paginator('list_multipart_uploads')
    # Works similar with build_full_result()
    pages = paginator.paginate(PaginationConfig={'MaxItems': 1},
                               Bucket=bucket_name)
    full_result = await pages.build_full_result()
    assert len(full_result['Uploads']) == 1


@pytest.mark.moto
@pytest.mark.asyncio
async def test_paginate_within_page_boundaries(
        s3_client, create_object, bucket_name):
    await create_object('a')
    await create_object('b')
    await create_object('c')
    await create_object('d')
    paginator = s3_client.get_paginator('list_objects')
    # First do it without a max keys so we're operating on a single page of
    # results.
    pages = paginator.paginate(PaginationConfig={'MaxItems': 1},
                               Bucket=bucket_name)
    first = await pages.build_full_result()
    t1 = first['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t1},
        Bucket=bucket_name)
    second = await pages.build_full_result()
    t2 = second['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t2},
        Bucket=bucket_name)
    third = await pages.build_full_result()
    t3 = third['NextToken']

    pages = paginator.paginate(
        PaginationConfig={'MaxItems': 1, 'StartingToken': t3},
        Bucket=bucket_name)
    fourth = await pages.build_full_result()

    assert first['Contents'][-1]['Key'] == 'a'
    assert second['Contents'][-1]['Key'] == 'b'
    assert third['Contents'][-1]['Key'] == 'c'
    assert fourth['Contents'][-1]['Key'] == 'd'


@pytest.mark.asyncio
@pytest.mark.parametrize('mocking_test', [False])
async def test_unicode_key_put_list(s3_client, bucket_name, create_object):
    # Verify we can upload a key with a unicode char and list it as well.
    key_name = u'\u2713'
    await create_object(key_name)
    parsed = await s3_client.list_objects(Bucket=bucket_name)
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == key_name
    parsed = await s3_client.get_object(Bucket=bucket_name, Key=key_name)
    data = await parsed['Body'].read()
    parsed['Body'].close()
    assert data == b'foo'


@pytest.mark.asyncio
@pytest.mark.parametrize('mocking_test', [False])
async def test_unicode_system_character(s3_client, bucket_name, create_object):
    # Verify we can use a unicode system character which would normally
    # break the xml parser
    key_name = 'foo\x08'
    await create_object(key_name)
    parsed = await s3_client.list_objects(Bucket=bucket_name)
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == key_name

    parsed = await s3_client.list_objects(
        Bucket=bucket_name, EncodingType='url')
    assert len(parsed['Contents']) == 1
    assert parsed['Contents'][0]['Key'] == 'foo%08'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_non_normalized_key_paths(s3_client, bucket_name, create_object):
    # The create_object method has assertEqual checks for 200 status.
    await create_object('key./././name')
    bucket = await s3_client.list_objects(Bucket=bucket_name)
    bucket_contents = bucket['Contents']
    assert len(bucket_contents) == 1
    assert bucket_contents[0]['Key'] == 'key./././name'


@pytest.mark.skipif(True, reason='Not supported')
@pytest.mark.asyncio
async def test_reset_stream_on_redirects(region, create_bucket):
    # Create a bucket in a non classic region.
    bucket_name = await create_bucket(region)
    # Then try to put a file like object to this location.
    assert bucket_name


@pytest.mark.moto
@pytest.mark.asyncio
async def test_copy_with_quoted_char(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo'
    await create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    source = '%s/%s' % (bucket_name, key_name)
    await s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2, CopySource=source)

    # Now verify we can retrieve the copied object.
    resp = await s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = await resp['Body'].read()
    resp['Body'].close()
    assert data == b'foo'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_copy_with_query_string(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo?notVersionid=bar'
    await create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    await s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2,
        CopySource='%s/%s' % (bucket_name, key_name))

    # Now verify we can retrieve the copied object.
    resp = await s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = await resp['Body'].read()
    resp['Body'].close()
    assert data == b'foo'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_can_copy_with_dict_form(s3_client, create_object, bucket_name):
    key_name = 'a+b/foo?versionId=abcd'
    await create_object(key_name=key_name)

    key_name2 = key_name + 'bar'
    await s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2,
        CopySource={'Bucket': bucket_name, 'Key': key_name})

    # Now verify we can retrieve the copied object.
    resp = await s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = await resp['Body'].read()
    resp['Body'].close()
    assert data == b'foo'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_can_copy_with_dict_form_with_version(
        s3_client, create_object, bucket_name):
    key_name = 'a+b/foo?versionId=abcd'
    response = await create_object(key_name=key_name)
    key_name2 = key_name + 'bar'
    await s3_client.copy_object(
        Bucket=bucket_name, Key=key_name2,
        CopySource={'Bucket': bucket_name, 'Key': key_name,
                    'VersionId': response["VersionId"]})

    # Now verify we can retrieve the copied object.
    resp = await s3_client.get_object(Bucket=bucket_name, Key=key_name2)
    data = await resp['Body'].read()
    resp['Body'].close()
    assert data == b'foo'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_copy_with_s3_metadata(s3_client, create_object, bucket_name):
    key_name = 'foo.txt'
    await create_object(key_name=key_name)
    copied_key = 'copied.txt'
    parsed = await s3_client.copy_object(
        Bucket=bucket_name, Key=copied_key,
        CopySource='%s/%s' % (bucket_name, key_name),
        MetadataDirective='REPLACE',
        Metadata={"mykey": "myvalue", "mykey2": "myvalue2"})
    pytest.aio.assert_status_code(parsed, 200)


@pytest.mark.parametrize('region', ['us-east-1'])
@pytest.mark.parametrize('signature_version', ['s3'])
# 'Content-Disposition' not supported by moto yet
@pytest.mark.parametrize('mocking_test', [False])
@pytest.mark.asyncio
async def test_presign_with_existing_query_string_values(
        s3_client, bucket_name, aio_session, create_object):
    key_name = 'foo.txt'
    await create_object(key_name=key_name)
    content_disposition = 'attachment; filename=foo.txt;'
    params = {'Bucket': bucket_name,
              'Key': key_name,
              'ResponseContentDisposition': content_disposition}
    presigned_url = s3_client.generate_presigned_url(
        'get_object', Params=params)
    # Try to retrieve the object using the presigned url.

    resp = await aio_session.get(presigned_url)
    data = await resp.read()
    await resp.close()
    assert resp.headers['Content-Disposition'] == content_disposition
    assert data == b'foo'


@pytest.mark.parametrize('region', ['us-east-1'])
@pytest.mark.parametrize('signature_version', ['s3v4'])
# moto host will be localhost
@pytest.mark.parametrize('mocking_test', [False])
@pytest.mark.asyncio
async def test_presign_sigv4(s3_client, bucket_name, aio_session,
                             create_object):
    key = 'myobject'
    await create_object(key_name=key)
    presigned_url = s3_client.generate_presigned_url(
        'get_object', Params={'Bucket': bucket_name, 'Key': key})
    msg = "Host was suppose to be the us-east-1 endpoint, " \
          "instead got: %s" % presigned_url
    assert presigned_url.startswith('https://s3.amazonaws.com/%s/%s'
                                    % (bucket_name, key)), msg

    # Try to retrieve the object using the presigned url.
    resp = await aio_session.get(presigned_url)
    data = await resp.read()
    assert data == b'foo'


@pytest.mark.parametrize('signature_version', ['s3v4'])
@pytest.mark.parametrize('mocking_test', [False])
@pytest.mark.asyncio
async def test_can_follow_signed_url_redirect(alternative_s3_client,
                                              create_object, bucket_name):
    await create_object('foobarbaz')

    # Simulate redirection by provide wrong endpoint intentionally
    resp = await alternative_s3_client.get_object(
        Bucket=bucket_name, Key='foobarbaz')
    data = await resp['Body'].read()
    resp['Body'].close()
    assert data == b'foo'


@pytest.mark.parametrize('region', ['eu-west-1'])
@pytest.mark.parametrize('alternative_region', ['us-west-2'])
@pytest.mark.parametrize('mocking_test', [False])
@pytest.mark.asyncio
async def test_bucket_redirect(
        s3_client, alternative_s3_client, region, create_bucket):
    key = 'foobarbaz'

    # create bucket in alternative region
    bucket_name = await create_bucket(region)

    await s3_client.put_object(Bucket=bucket_name, Key=key, Body=b'')
    await s3_client.get_object(Bucket=bucket_name, Key=key)

    # This should not raise
    await alternative_s3_client.put_object(Bucket=bucket_name, Key=key,
                                           Body=b'')
    await alternative_s3_client.get_object(Bucket=bucket_name, Key=key)


@pytest.mark.parametrize('signature_version', ['s3v4'])
@pytest.mark.asyncio
@pytest.mark.moto
async def test_head_object_keys(s3_client, create_object, bucket_name):
    await create_object('foobarbaz')

    resp = await s3_client.head_object(
        Bucket=bucket_name, Key='foobarbaz')

    # this is to ensure things like:
    # https://github.com/aio-libs/aiobotocore/issues/131 don't happen again
    assert set(resp.keys()) == {
        'ETag', 'ContentType', 'Metadata', 'LastModified',
        'ResponseMetadata', 'ContentLength', 'VersionId'}
