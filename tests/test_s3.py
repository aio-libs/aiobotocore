from functools import wraps
import os
import time
import random
import asyncio
import tempfile
import shutil
import unittest
from unittest import mock
import contextlib
from botocore.vendored.requests import adapters
from botocore.vendored.requests.exceptions import ConnectionError
from botocore.compat import six
import botocore.auth
import botocore.credentials
import botocore.vendored.requests as requests
from botocore.client import Config
import aiobotocore


def run_until_complete(fun):
    if not asyncio.iscoroutinefunction(fun):
        fun = asyncio.coroutine(fun)

    @wraps(fun)
    def wrapper(test, *args, **kw):
        loop = test.loop
        ret = loop.run_until_complete(
            asyncio.wait_for(fun(test, *args, **kw), 150, loop=loop))
        return ret

    return wrapper


@contextlib.contextmanager
def temporary_file(mode):
    """This is a cross platform temporary file creation.

    tempfile.NamedTemporary file on windows creates a secure temp file
    that can't be read by other processes and can't be opened a second time.

    For tests, we generally *want* them to be read multiple times.
    The test fixture writes the temp file contents, the test reads the
    temp file.

    """
    temporary_directory = tempfile.mkdtemp()
    basename = 'tmpfile-%s-%s' % (int(time.time()), random.randint(1, 1000))
    full_filename = os.path.join(temporary_directory, basename)
    open(full_filename, 'w').close()
    try:
        with open(full_filename, mode) as f:
            yield f
    finally:
        shutil.rmtree(temporary_directory)


class BaseTest(unittest.TestCase):
    """Base test case for unittests.
    """

    def setUp(self):
        asyncio.set_event_loop(None)
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.doCleanups()
        self.loop.close()
        del self.loop


class BaseS3ClientTest(BaseTest):
    def setUp(self):
        super().setUp()

        self.session = aiobotocore.get_session(loop=self.loop)
        self.region = 'us-east-1'
        self.client = self.session.create_client('s3', region_name=self.region)
        self.keys = []
        self.addCleanup(self.client.close)

    def assert_status_code(self, response, status_code):
        self.assertEqual(
            response['ResponseMetadata']['HTTPStatusCode'],
            status_code
        )

    @asyncio.coroutine
    def create_bucket(self, bucket_name=None):
        bucket_kwargs = {}
        bucket_name = 'aiobotocoretest1432895895-204'
        if bucket_name is None:
            bucket_name = 'aiobotocoretest%s-%s' % (int(time.time()),
                                                    random.randint(1, 1000))
        bucket_kwargs = {'Bucket': bucket_name}
        if self.region != 'us-east-1':
            bucket_kwargs['CreateBucketConfiguration'] = {
                'LocationConstraint': self.region,
            }
        response = yield from self.client.create_bucket(**bucket_kwargs)
        self.assert_status_code(response, 200)
        self.addCleanup(self.loop.run_until_complete,
                        self.delete_bucket(bucket_name))
        return bucket_name

    @asyncio.coroutine
    def create_object(self, key_name, body='foo'):
        self.keys.append(key_name)
        yield from self.client.put_object(
            Bucket=self.bucket_name, Key=key_name,
            Body=body)

    @asyncio.coroutine
    def create_multipart_upload(self, key_name):
        parsed = yield from self.client.create_multipart_upload(
            Bucket=self.bucket_name, Key=key_name)
        upload_id = parsed['UploadId']
        self.addCleanup(
            self.client.abort_multipart_upload,
            UploadId=upload_id,
            Bucket=self.bucket_name, Key=key_name)

    @asyncio.coroutine
    def abort_multipart_upload(self, bucket_name, key, upload_id):
        yield from self.client.abort_multipart_upload(
            UploadId=upload_id, Bucket=self.bucket_name, Key=key)

    @asyncio.coroutine
    def delete_object(self, key, bucket_name):
        response = yield from self.client.delete_object(
            Bucket=bucket_name, Key=key)
        self.assert_status_code(response, 204)

    @asyncio.coroutine
    def delete_bucket(self, bucket_name):
        response = yield from self.client.delete_bucket(Bucket=bucket_name)
        self.assert_status_code(response, 204)

    @asyncio.coroutine
    def create_object_catch_exceptions(self, key_name):
        try:
            yield from self.create_object(key_name=key_name)
        except Exception as e:
            self.caught_exceptions.append(e)

    @asyncio.coroutine
    def assert_num_uploads_found(self, operation, num_uploads,
                                 max_items=None, num_attempts=5):
        amount_seen = None
        paginator = self.client.get_paginator(operation)
        for _ in range(num_attempts):
            pages = paginator.paginate(Bucket=self.bucket_name,
                                       max_items=max_items)
            iterators = pages.result_key_iters()
            self.assertEqual(len(iterators), 2)
            self.assertEqual(iterators[0].result_key.expression, 'Uploads')
            # It sometimes takes a while for all the uploads to show up,
            # especially if the upload was just created.  If we don't
            # see the expected amount, we retry up to num_attempts time
            # before failing.
            amount_seen = len(list(iterators[0]))
            if amount_seen == num_uploads:
                # Test passed.
                return
            else:
                # Sleep and try again.
                time.sleep(2)
        self.fail("Expected to see %s uploads, instead saw: %s" % (
            num_uploads, amount_seen))


class TestS3BaseWithBucket(BaseS3ClientTest):
    def setUp(self):
        super().setUp()
        self.bucket_name = self.loop.run_until_complete(self.create_bucket())


class TestS3Buckets(TestS3BaseWithBucket):
    @run_until_complete
    def test_can_make_request(self):
        # Basic smoke test to ensure we can talk to s3.
        result = yield from self.client.list_buckets()
        # Can't really assume anything about whether or not they have buckets,
        # but we can assume something about the structure of the response.
        self.assertEqual(sorted(list(result.keys())),
                         ['Buckets', 'Owner', 'ResponseMetadata'])

    @run_until_complete
    def test_can_get_bucket_location(self):
        result = yield from self.client.get_bucket_location(
            Bucket=self.bucket_name)
        self.assertIn('LocationConstraint', result)
        # For buckets in us-east-1 (US Classic Region) this will be None
        self.assertEqual(result['LocationConstraint'], None)


class TestS3Objects(TestS3BaseWithBucket):
    def tearDown(self):
        for key in self.keys:
            self.loop.run_until_complete(self.client.delete_object(
                Bucket=self.bucket_name, Key=key))
        super().tearDown()

    def increment_auth(self, request, **kwargs):
        self.auth_paths.append(request.auth_path)

    @run_until_complete
    def test_can_delete_urlencoded_object(self):
        key_name = 'a+b/foo'
        yield from self.create_object(key_name=key_name)
        self.keys.pop()
        resp = yield from self.client.list_objects(
            Bucket=self.bucket_name)
        bucket_contents = resp['Contents']

        self.assertEqual(len(bucket_contents), 1)
        self.assertEqual(bucket_contents[0]['Key'], 'a+b/foo')

        resp = yield from self.client.list_objects(
            Bucket=self.bucket_name, Prefix='a+b')
        subdir_contents = resp['Contents']
        self.assertEqual(len(subdir_contents), 1)
        self.assertEqual(subdir_contents[0]['Key'], 'a+b/foo')

        response = yield from self.client.delete_object(
            Bucket=self.bucket_name, Key=key_name)
        self.assert_status_code(response, 204)

    # @attr('slow')
    @run_until_complete
    def test_can_paginate(self):
        coros = []
        for i in range(5):
            key_name = 'key%s' % i
            coros.append(self.create_object(key_name))
        yield from asyncio.gather(*coros, loop=self.loop)
        # Eventual consistency.
        yield from asyncio.sleep(4, loop=self.loop)
        paginator = self.client.get_paginator('list_objects')

        pager = paginator.paginate(MaxKeys=1, Bucket=self.bucket_name)
        responses = []
        while True:
            response = yield from pager.next_page()
            if not response:
                break
            responses.append(response)

        self.assertEqual(len(responses), 5, responses)
        key_names = [el['Contents'][0]['Key']
                     for el in responses]
        self.assertEqual(key_names, ['key0', 'key1', 'key2', 'key3', 'key4'])

    # @attr('slow')
    @run_until_complete
    def test_can_paginate_with_page_size(self):
        coros = []
        for i in range(5):
            key_name = 'key%s' % i
            coros.append(self.create_object(key_name))
        yield from asyncio.gather(*coros, loop=self.loop)
        # Eventual consistency.
        yield from asyncio.sleep(4, loop=self.loop)

        paginator = self.client.get_paginator('list_objects')
        pager = paginator.paginate(page_size=1, Bucket=self.bucket_name)
        responses = []
        while True:
            response = yield from pager.next_page()
            if not response:
                break
            responses.append(response)

        self.assertEqual(len(responses), 5, responses)
        data = [r for r in responses]
        key_names = [el['Contents'][0]['Key']
                     for el in data]
        self.assertEqual(key_names, ['key0', 'key1', 'key2', 'key3', 'key4'])

    # @attr('slow')
    # @run_until_complete
    # def test_result_key_iters(self):
    # coros = []
    #     for i in range(5):
    #         key_name = 'key/%s/%s' % (i, i)
    #         coros.append(self.create_object(key_name))
    #         key_name2 = 'key/%s' % i
    #         coros.append(self.create_object(key_name2))
    #     yield from asyncio.gather(*coros, loop=self.loop)
    #     # Eventual consistency.
    #     yield from asyncio.sleep(4, loop=self.loop)
    #
    #     paginator = self.client.get_paginator('list_objects')
    #     generator = paginator.paginate(MaxKeys=2,
    #                                    Prefix='key/',
    #                                    Delimiter='/',
    #                                    Bucket=self.bucket_name)
    #     iterators = yield from generator.result_key_iters()
    #
    #     response = defaultdict(list)
    #     key_names = [i.result_key for i in iterators]
    #     for vals in zip_longest(*iterators):
    #         for k, val in zip(key_names, vals):
    #             response.setdefault(k.expression, [])
    #             response[k.expression].append(val)
    #     self.assertIn('Contents', response)
    #     self.assertIn('CommonPrefixes', response)

    @run_until_complete
    def test_can_get_and_put_object(self):
        yield from self.create_object('foobarbaz', body='body contents')
        yield from asyncio.sleep(3, loop=self.loop)
        data = yield from self.client.get_object(
            Bucket=self.bucket_name, Key='foobarbaz')
        payload = yield from data['Body'].read()
        self.assertEqual(payload.decode('utf-8'), 'body contents')

    @run_until_complete
    def test_get_object_stream_wrapper(self):
        yield from self.create_object('foobarbaz', body='body contents')
        response = yield from self.client.get_object(
            Bucket=self.bucket_name, Key='foobarbaz')
        body = response['Body']
        # I am NOT able to set a socket timeout
        # body.set_socket_timeout(10)
        data = yield from body.read(1)
        self.assertEqual(data.decode('utf-8'), 'b')
        data = yield from body.read()
        self.assertEqual(data.decode('utf-8'), 'ody contents')

    # def test_paginate_max_items(self):
    #     yield from self.create_multipart_upload('foo/key1')
    #     yield from self.create_multipart_upload('foo/key1')
    #     yield from self.create_multipart_upload('foo/key1')
    #     yield from self.create_multipart_upload('foo/key2')
    #     yield from self.create_multipart_upload('foobar/key1')
    #     yield from self.create_multipart_upload('foobar/key2')
    #     yield from self.create_multipart_upload('bar/key1')
    #     yield from self.create_multipart_upload('bar/key2')
    #
    #     # Verify when we have max_items=None, we get back all 8 uploads.
    #     self.assert_num_uploads_found('list_multipart_uploads',
    #                                   max_items=None, num_uploads=8)
    #
    #     # Verify when we have max_items=1, we get back 1 upload.
    #     self.assert_num_uploads_found('list_multipart_uploads',
    #                                   max_items=1, num_uploads=1)
    #
    #     paginator = self.client.get_paginator('list_multipart_uploads')
    #     # Works similar with build_full_result()
    #     pages = paginator.paginate(max_items=1,
    #                                Bucket=self.bucket_name)
    #     full_result = pages.build_full_result()
    #     self.assertEqual(len(full_result['Uploads']), 1)

    @run_until_complete
    def test_paginate_within_page_boundaries(self):
        yield from self.create_object('a')
        yield from self.create_object('b')
        yield from self.create_object('c')
        yield from self.create_object('d')
        paginator = self.client.get_paginator('list_objects')
        # First do it without a max keys so we're operating on a single page of
        # results.
        pages = paginator.paginate(max_items=1,
                                   Bucket=self.bucket_name)
        first = yield from pages.build_full_result()
        t1 = first['NextToken']

        pages = paginator.paginate(max_items=1,
                                   starting_token=t1,
                                   Bucket=self.bucket_name)
        second = yield from pages.build_full_result()
        t2 = second['NextToken']

        pages = yield from paginator.paginate(max_items=1,
                                              starting_token=t2,
                                              Bucket=self.bucket_name)
        third = yield from pages.build_full_result()
        t3 = third['NextToken']

        pages = paginator.paginate(max_items=1,
                                   starting_token=t3,
                                   Bucket=self.bucket_name)
        fourth = yield from pages.build_full_result()

        self.assertEqual(first['Contents'][-1]['Key'], 'a')
        self.assertEqual(second['Contents'][-1]['Key'], 'b')
        self.assertEqual(third['Contents'][-1]['Key'], 'c')
        self.assertEqual(fourth['Contents'][-1]['Key'], 'd')

    @run_until_complete
    def test_unicode_key_put_list(self):
        # Verify we can upload a key with a unicode char and list it as well.
        key_name = u'\u2713'
        yield from self.create_object(key_name)
        parsed = yield from self.client.list_objects(Bucket=self.bucket_name)
        self.assertEqual(len(parsed['Contents']), 1)
        self.assertEqual(parsed['Contents'][0]['Key'], key_name)
        parsed = yield from self.client.get_object(
            Bucket=self.bucket_name, Key=key_name)
        data = yield from parsed['Body'].read()
        self.assertEqual(data.decode('utf-8'), 'foo')

    # def test_thread_safe_auth(self):
    #     self.auth_paths = []
    #     self.caught_exceptions = []
    #     self.session.register('before-sign', self.increment_auth)
    #     self.client = self.session.create_client('s3', self.region)
    #     self.create_object(key_name='foo1')
    #     threads = []
    #     for i in range(10):
    #         t = threading.Thread(target=self.create_object_catch_exceptions,
    #                              args=('foo%s' % i,))
    #         t.daemon = True
    #         threads.append(t)
    #     for thread in threads:
    #         thread.start()
    #     for thread in threads:
    #         thread.join()
    #     self.assertEqual(
    #         self.caught_exceptions, [],
    #         "Unexpectedly caught exceptions: %s" % self.caught_exceptions)
    #     self.assertEqual(
    #         len(set(self.auth_paths)), 10,
    #         "Expected 10 unique auth paths, instead received: %s" %
    #         (self.auth_paths))

    @run_until_complete
    def test_non_normalized_key_paths(self):
        # The create_object method has assertEqual checks for 200 status.
        yield from self.create_object('key./././name')
        resp = yield from self.client.list_objects(Bucket=self.bucket_name)
        bucket_contents = resp['Contents']
        self.assertEqual(len(bucket_contents), 1)
        self.assertEqual(bucket_contents[0]['Key'], 'key./././name')


class TestS3Regions(BaseS3ClientTest):
    def setUp(self):
        super().setUp()

        self.tempdir = tempfile.mkdtemp()
        self.region = 'us-west-2'

    def tearDown(self):
        shutil.rmtree(self.tempdir)
        super().tearDown()

    def test_reset_stream_on_redirects(self):
        # Create a bucket in a non classic region.
        bucket_name = self.create_bucket()
        # Then try to put a file like object to this location.
        filename = os.path.join(self.tempdir, 'foo')
        with open(filename, 'wb') as f:
            f.write(b'foo' * 1024)
        with open(filename, 'rb') as f:
            self.client.put_object(
                Bucket=bucket_name, Key='foo', Body=f)

        self.addCleanup(self.delete_object, key='foo',
                        bucket_name=bucket_name)

        data = self.client.get_object(
            Bucket=bucket_name, Key='foo')
        self.assertEqual(data['Body'].read(), b'foo' * 1024)


class TestS3Copy(TestS3BaseWithBucket):
    def tearDown(self):
        for key in self.keys:
            self.client.delete_object(
                Bucket=self.bucket_name, Key=key)
        super().tearDown()

    def test_copy_with_quoted_char(self):
        key_name = 'a+b/foo'
        self.create_object(key_name=key_name)

        key_name2 = key_name + 'bar'
        self.client.copy_object(
            Bucket=self.bucket_name, Key=key_name + 'bar',
            CopySource='%s/%s' % (self.bucket_name, key_name))
        self.keys.append(key_name2)

        # Now verify we can retrieve the copied object.
        data = self.client.get_object(
            Bucket=self.bucket_name, Key=key_name + 'bar')
        self.assertEqual(data['Body'].read().decode('utf-8'), 'foo')

    def test_copy_with_s3_metadata(self):
        key_name = 'foo.txt'
        self.create_object(key_name=key_name)
        copied_key = 'copied.txt'
        parsed = self.client.copy_object(
            Bucket=self.bucket_name, Key=copied_key,
            CopySource='%s/%s' % (self.bucket_name, key_name),
            MetadataDirective='REPLACE',
            Metadata={"mykey": "myvalue", "mykey2": "myvalue2"})
        self.keys.append(copied_key)
        self.assert_status_code(parsed, 200)


class BaseS3PresignTest(BaseS3ClientTest):
    def tearDown(self):
        for key in self.keys:
            self.client.delete_object(
                Bucket=self.bucket_name, Key=key)
        super().tearDown()

    def setup_bucket(self):
        self.key = 'myobject'
        self.bucket_name = self.create_bucket()
        self.create_object(key_name=self.key)


class TestS3PresignUsStandard(BaseS3PresignTest):
    def setUp(self):
        super().setUp()
        self.region = 'us-east-1'
        self.client_config = Config(
            region_name=self.region, signature_version='s3')
        self.client = self.session.create_client(
            's3', config=self.client_config)
        self.setup_bucket()

    def test_presign_sigv2(self):
        presigned_url = self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket_name, 'Key': self.key})
        self.assertTrue(
            presigned_url.startswith(
                'https://%s.s3.amazonaws.com/%s' % (
                    self.bucket_name, self.key)),
            "Host was suppose to use DNS style, instead "
            "got: %s" % presigned_url)
        # Try to retrieve the object using the presigned url.
        self.assertEqual(requests.get(presigned_url).content, b'foo')

    def test_presign_sigv4(self):
        self.client_config.signature_version = 's3v4'
        self.client = self.session.create_client(
            's3', config=self.client_config)
        presigned_url = self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket_name, 'Key': self.key})
        self.assertTrue(
            presigned_url.startswith(
                'https://s3.amazonaws.com/%s/%s' % (
                    self.bucket_name, self.key)),
            "Host was suppose to be the us-east-1 endpoint, instead "
            "got: %s" % presigned_url)
        # Try to retrieve the object using the presigned url.
        self.assertEqual(requests.get(presigned_url).content, b'foo')

    def test_presign_post_sigv2(self):
        # Create some of the various supported conditions.
        conditions = [
            {"acl": "public-read"},
        ]

        # Create the fields that follow the policy.
        fields = {
            'acl': 'public-read',
        }

        # Retrieve the args for the presigned post.
        post_args = self.client.generate_presigned_post(
            self.bucket_name, self.key, Fields=fields,
            Conditions=conditions)

        # Make sure that the form can be posted successfully.
        files = {'file': ('baz', 'some data')}

        # Make sure the correct endpoint is being used
        self.assertTrue(
            post_args['url'].startswith(
                'https://%s.s3.amazonaws.com' % self.bucket_name),
            "Host was suppose to use DNS style, instead "
            "got: %s" % post_args['url'])

        # Try to retrieve the object using the presigned url.
        r = requests.post(
            post_args['url'], data=post_args['fields'], files=files)
        self.assertEqual(r.status_code, 204)

    def test_presign_post_sigv4(self):
        self.client_config.signature_version = 's3v4'
        self.client = self.session.create_client(
            's3', config=self.client_config)

        # Create some of the various supported conditions.
        conditions = [
            {"acl": 'public-read'},
        ]

        # Create the fields that follow the policy.
        fields = {
            'acl': 'public-read',
        }

        # Retrieve the args for the presigned post.
        post_args = self.client.generate_presigned_post(
            self.bucket_name, self.key, Fields=fields,
            Conditions=conditions)

        # Make sure that the form can be posted successfully.
        files = {'file': ('baz', 'some data')}

        # Make sure the correct endpoint is being used
        self.assertTrue(
            post_args['url'].startswith(
                'https://s3.amazonaws.com/%s' % self.bucket_name),
            "Host was suppose to use us-east-1 endpoint, instead "
            "got: %s" % post_args['url'])

        r = requests.post(
            post_args['url'], data=post_args['fields'], files=files)
        self.assertEqual(r.status_code, 204)


class TestS3PresignNonUsStandard(BaseS3PresignTest):
    def setUp(self):
        super().setUp()
        self.region = 'us-west-2'
        self.client_config = Config(
            region_name=self.region, signature_version='s3')
        self.client = self.session.create_client(
            's3', config=self.client_config)
        self.setup_bucket()

    def test_presign_sigv2(self):
        presigned_url = self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket_name, 'Key': self.key})
        self.assertTrue(
            presigned_url.startswith(
                'https://%s.s3.amazonaws.com/%s' % (
                    self.bucket_name, self.key)),
            "Host was suppose to use DNS style, instead "
            "got: %s" % presigned_url)
        # Try to retrieve the object using the presigned url.
        self.assertEqual(requests.get(presigned_url).content, b'foo')

    def test_presign_sigv4(self):
        self.client_config.signature_version = 's3v4'
        self.client = self.session.create_client(
            's3', config=self.client_config)
        presigned_url = self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket_name, 'Key': self.key})

        self.assertTrue(
            presigned_url.startswith(
                'https://s3-us-west-2.amazonaws.com/%s/%s' % (
                    self.bucket_name, self.key)),
            "Host was suppose to be the us-west-2 endpoint, instead "
            "got: %s" % presigned_url)
        # Try to retrieve the object using the presigned url.
        self.assertEqual(requests.get(presigned_url).content, b'foo')

    def test_presign_post_sigv2(self):
        # Create some of the various supported conditions.
        conditions = [
            {"acl": "public-read"},
        ]

        # Create the fields that follow the policy.
        fields = {
            'acl': 'public-read',
        }

        # Retrieve the args for the presigned post.
        post_args = self.client.generate_presigned_post(
            self.bucket_name, self.key, Fields=fields, Conditions=conditions)

        # Make sure that the form can be posted successfully.
        files = {'file': ('baz', 'some data')}

        # Make sure the correct endpoint is being used
        self.assertTrue(
            post_args['url'].startswith(
                'https://%s.s3.amazonaws.com' % self.bucket_name),
            "Host was suppose to use DNS style, instead "
            "got: %s" % post_args['url'])

        r = requests.post(
            post_args['url'], data=post_args['fields'], files=files)
        self.assertEqual(r.status_code, 204)

    def test_presign_post_sigv4(self):
        self.client_config.signature_version = 's3v4'
        self.client = self.session.create_client(
            's3', config=self.client_config)

        # Create some of the various supported conditions.
        conditions = [
            {"acl": "public-read"},
        ]

        # Create the fields that follow the policy.
        fields = {
            'acl': 'public-read',
        }

        # Retrieve the args for the presigned post.
        post_args = self.client.generate_presigned_post(
            self.bucket_name, self.key, Fields=fields, Conditions=conditions)

        # Make sure that the form can be posted successfully.
        files = {'file': ('baz', 'some data')}

        # Make sure the correct endpoint is being used
        self.assertTrue(
            post_args['url'].startswith(
                'https://s3-us-west-2.amazonaws.com/%s' % self.bucket_name),
            "Host was suppose to use DNS style, instead "
            "got: %s" % post_args['url'])

        r = requests.post(
            post_args['url'], data=post_args['fields'], files=files)
        self.assertEqual(r.status_code, 204)


class TestCreateBucketInOtherRegion(TestS3BaseWithBucket):
    def tearDown(self):
        for key in self.keys:
            self.client.delete_object(
                Bucket=self.bucket_name, Key=key)

    @run_until_complete
    def test_bucket_in_other_region(self):
        # This verifies expect 100-continue behavior.  We previously
        # had a bug where we did not support this behavior and trying to
        # create a bucket and immediately PutObject with a file like object
        # would actually cause errors.
        client = self.session.create_client('s3', 'us-east-1')
        with temporary_file('w') as f:
            f.write('foobarbaz' * 1024 * 1024)
            f.flush()
            with open(f.name, 'rb') as body_file:
                response = client.put_object(
                    Bucket=self.bucket_name,
                    Key='foo.txt', Body=body_file)
            self.assert_status_code(response, 200)
            self.keys.append('foo.txt')

    @run_until_complete
    def test_bucket_in_other_region_using_http(self):
        client = self.session.create_client(
            's3', 'us-east-1', endpoint_url='http://s3.amazonaws.com/')
        with temporary_file('w') as f:
            f.write('foobarbaz' * 1024 * 1024)
            f.flush()
            with open(f.name, 'rb') as body_file:
                response = client.put_object(
                    Bucket=self.bucket_name,
                    Key='foo.txt', Body=body_file)
            self.assert_status_code(response, 200)
            self.keys.append('foo.txt')


class TestS3SigV4Client(BaseS3ClientTest):
    def setUp(self):
        super().setUp()
        self.region = 'eu-central-1'
        self.client = self.session.create_client('s3', self.region)
        self.bucket_name = self.loop.run_until_complete(self.create_bucket())
        self.keys = []

    def tearDown(self):
        for key in self.keys:
            self.loop.run_until_complete(
                self.delete_object(bucket_name=self.bucket_name, key=key))
        super().tearDown()

    @run_until_complete
    def test_can_get_bucket_location(self):
        # Even though the bucket is in eu-central-1, we should still be able to
        # use the us-east-1 endpoint class to get the bucket location.
        client = self.session.create_client('s3', 'us-east-1')
        # Also keep in mind that while this test is useful, it doesn't test
        # what happens once DNS propogates which is arguably more interesting,
        # as DNS will point us to the eu-central-1 endpoint.
        response = yield from client.get_bucket_location(
            Bucket=self.bucket_name)
        self.assertEqual(response['LocationConstraint'], 'eu-central-1')

    @run_until_complete
    def test_request_retried_for_sigv4(self):
        body = six.BytesIO(b"Hello world!")

        original_send = adapters.HTTPAdapter.send
        state = mock.Mock()
        state.error_raised = False

        def mock_http_adapter_send(self, *args, **kwargs):
            if not state.error_raised:
                state.error_raised = True
                raise ConnectionError("Simulated ConnectionError raised.")
            else:
                return original_send(self, *args, **kwargs)

        with mock.patch('botocore.vendored.requests.adapters.HTTPAdapter.send',
                        mock_http_adapter_send):
            response = self.client.put_object(Bucket=self.bucket_name,
                                              Key='foo.txt', Body=body)
            self.assert_status_code(response, 200)
            self.keys.append('foo.txt')

    # @attr('slow')
    # def test_paginate_list_objects_unicode(self):
    # key_names = [
    #         u'non-ascii-key-\xe4\xf6\xfc-01.txt',
    #         u'non-ascii-key-\xe4\xf6\xfc-02.txt',
    #         u'non-ascii-key-\xe4\xf6\xfc-03.txt',
    #         u'non-ascii-key-\xe4\xf6\xfc-04.txt',
    #     ]
    #     for key in key_names:
    #         response = self.client.put_object(Bucket=self.bucket_name,
    #                                           Key=key, Body='')
    #         self.assert_status_code(response, 200)
    #         self.keys.append(key)
    #
    #     list_objs_paginator = self.client.get_paginator('list_objects')
    #     key_refs = []
    #     for response in list_objs_paginator.paginate(Bucket=self.bucket_name,
    #                                                  page_size=2):
    #         for content in response['Contents']:
    #             key_refs.append(content['Key'])
    #
    #     self.assertEqual(key_names, key_refs)

    # @attr('slow')
    # def test_paginate_list_objects_safe_chars(self):
    #     key_names = [
    #         u'-._~safe-chars-key-01.txt',
    #         u'-._~safe-chars-key-02.txt',
    #         u'-._~safe-chars-key-03.txt',
    #         u'-._~safe-chars-key-04.txt',
    #     ]
    #     for key in key_names:
    #         response = self.client.put_object(Bucket=self.bucket_name,
    #                                           Key=key, Body='')
    #         self.assert_status_code(response, 200)
    #         self.keys.append(key)
    #
    #     list_objs_paginator = self.client.get_paginator('list_objects')
    #     key_refs = []
    #     for response in list_objs_paginator.paginate(Bucket=self.bucket_name,
    #                                                  page_size=2):
    #         for content in response['Contents']:
    #             key_refs.append(content['Key'])
    #
    #     self.assertEqual(key_names, key_refs)

    @run_until_complete
    def test_create_multipart_upload(self):
        key = 'mymultipartupload'
        response = yield from self.client.create_multipart_upload(
            Bucket=self.bucket_name, Key=key
        )
        self.assert_status_code(response, 200)
        upload_id = response['UploadId']
        self.addCleanup(
            self.abort_multipart_upload,
            bucket_name=self.bucket_name, key=key, upload_id=upload_id
        )

        response = yield from self.client.list_multipart_uploads(
            Bucket=self.bucket_name, Prefix=key)

        # Make sure there is only one multipart upload.
        self.assertEqual(len(response['Uploads']), 1)
        # Make sure the upload id is as expected.
        self.assertEqual(response['Uploads'][0]['UploadId'], upload_id)


class TestCanSwitchToSigV4(BaseTest):
    def setUp(self):
        super().setUp()
        self.environ = {}
        self.environ_patch = mock.patch('os.environ', self.environ)
        self.environ_patch.start()
        self.session = botocore.session.get_session()
        self.tempdir = tempfile.mkdtemp()
        self.config_filename = os.path.join(self.tempdir, 'config_file')
        self.environ['AWS_CONFIG_FILE'] = self.config_filename

    def tearDown(self):
        self.environ_patch.stop()
        shutil.rmtree(self.tempdir)
        super().tearDown()


class TestSSEKeyParamValidation(BaseTest):
    def setUp(self):
        super().setUp()

        self.session = aiobotocore.get_session(loop=self.loop)
        self.client = self.session.create_client('s3', region_name='us-west-2')

        self.bucket_name = 'aiobotocoretest%s-%s' % (
            int(time.time()), random.randint(1, 1000))
        self.loop.run_until_complete(self.client.create_bucket(
            Bucket=self.bucket_name,
            CreateBucketConfiguration={
                'LocationConstraint': 'us-west-2',
            }
        ))
        self.addCleanup(self.loop.run_until_complete,
                        self.client.delete_bucket(Bucket=self.bucket_name))

    @run_until_complete
    def test_make_request_with_sse(self):
        key_bytes = os.urandom(32)
        # Obviously a bad key here, but we just want to ensure we can use
        # a str/unicode type as a key.
        key_str = 'abcd' * 8

        # Put two objects with an sse key, one with random bytes,
        # one with str/unicode.  Then verify we can GetObject() both
        # objects.
        yield from self.client.put_object(
            Bucket=self.bucket_name, Key='foo.txt',
            Body=b'mycontents', SSECustomerAlgorithm='AES256',
            SSECustomerKey=key_bytes)

        self.addCleanup(self.loop.run_until_complete,
                        self.client.delete_object(
                            Bucket=self.bucket_name,
                            Key='foo.txt'))
        yield from self.client.put_object(
            Bucket=self.bucket_name, Key='foo2.txt',
            Body=b'mycontents2', SSECustomerAlgorithm='AES256',
            SSECustomerKey=key_str)

        self.addCleanup(self.loop.run_until_complete,
                        self.client.delete_object(Bucket=self.bucket_name,
                                                  Key='foo2.txt'))

        resp = yield from self.client.get_object(Bucket=self.bucket_name,
                                                 Key='foo.txt',
                                                 SSECustomerAlgorithm='AES256',
                                                 SSECustomerKey=key_bytes)
        data = yield from resp['Body'].read()
        self.assertEqual(data, b'mycontents')
        resp = yield from self.client.get_object(Bucket=self.bucket_name,
                                                 Key='foo2.txt',
                                                 SSECustomerAlgorithm='AES256',
                                                 SSECustomerKey=key_str)

        data = yield from resp['Body'].read()
        self.assertEqual(data, b'mycontents2')


class TestS3UTF8Headers(BaseS3ClientTest):
    def test_can_set_utf_8_headers(self):
        bucket_name = self.create_bucket()
        body = six.BytesIO(b"Hello world!")

        response = yield from self.client.put_object(
            Bucket=bucket_name, Key="foo.txt", Body=body,
            ContentDisposition="attachment; filename=5小時接力起跑.jpg;")
        self.assert_status_code(response, 200)
        self.addCleanup(self.loop.run_until_complete,
                        self.client.delete_object(Bucket=bucket_name,
                                                  Key="foo.txt"))
