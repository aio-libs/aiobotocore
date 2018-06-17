aiobotocore
===========
.. image:: https://travis-ci.com/aio-libs/aiobotocore.svg?branch=master
    :target: https://travis-ci.com/aio-libs/aiobotocore
.. image:: https://codecov.io/gh/aio-libs/aiobotocore/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/aio-libs/aiobotocore
.. image:: https://img.shields.io/pypi/v/aiobotocore.svg
    :target: https://pypi.python.org/pypi/aiobotocore
.. image:: https://badges.gitter.im/Join%20Chat.svg
    :target: https://gitter.im/aio-libs/aiobotocore
    :alt: Chat on Gitter



Async client for amazon services using botocore_ and aiohttp_/asyncio_.

Main purpose of this library to support amazon s3 api, but other services
should work (may be with minor fixes). For now we have tested
only upload/download api for s3, other users report that SQS and Dynamo
services work also. More tests coming soon.


Install
-------
::

    $ pip install aiobotocore


Basic Example
-------------

.. code:: python

    import asyncio
    import aiobotocore

    AWS_ACCESS_KEY_ID = "xxx"
    AWS_SECRET_ACCESS_KEY = "xxx"


    async def go(loop):
        bucket = 'dataintake'
        filename = 'dummy.bin'
        folder = 'aiobotocore'
        key = '{}/{}'.format(folder, filename)

        session = aiobotocore.get_session(loop=loop)
        async with session.create_client('s3', region_name='us-west-2',
                                       aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                                       aws_access_key_id=AWS_ACCESS_KEY_ID) as client:
            # upload object to amazon s3
            data = b'\x01'*1024
            resp = await client.put_object(Bucket=bucket,
                                                Key=key,
                                                Body=data)
            print(resp)

            # getting s3 object properties of file we just uploaded
            resp = await client.get_object_acl(Bucket=bucket, Key=key)
            print(resp)

            # get object from s3
            response = await client.get_object(Bucket=bucket, Key=key)
            # this will ensure the connection is correctly re-used/closed
            async with response['Body'] as stream:
                assert await stream.read() == data

            # list s3 objects using paginator
            paginator = client.get_paginator('list_objects')
            async for result in paginator.paginate(Bucket=bucket, Prefix=folder):
                for c in result.get('Contents', []):
                    print(c)

            # delete object from s3
            resp = await client.delete_object(Bucket=bucket, Key=key)
            print(resp)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(go(loop))


Supported AWS Services
----------------------

This is a non-exuastive list of what tests aiobotocore runs against AWS services. Not all methods are tested but we aim to test the majority of
commonly used methods.

+----------------+-----------------------+
| Service        | Status                |
+================+=======================+
| S3             | Working               |
+----------------+-----------------------+
| DynamoDB       | Basic methods tested  |
+----------------+-----------------------+
| SNS            | Basic methods tested  |
+----------------+-----------------------+
| SQS            | Basic methods tested  |
+----------------+-----------------------+
| CloudFormation | Stack creation tested |
+----------------+-----------------------+
| Kinesis        | Basic methods tested  |
+----------------+-----------------------+

Due to the way boto3 is implemented, its highly likely that even if services are not listed above that you can take any `boto3.client('service')` and
stick `await` infront of methods to make them async, e.g. `await client.list_named_queries()` would asynchronous list all of the named Athena queries.

If a service is not listed here and you could do with some tests or examples feel free to raise an issue.

Run Tests
---------

Make sure you have development requirements installed and your amazon key and
secret accessible via environment variables:

::

    $ cd aiobotocore
    $ export AWS_ACCESS_KEY_ID=xxx
    $ export AWS_SECRET_ACCESS_KEY=xxx
    $ pip install -Ur requirements-dev.txt

Execute tests suite:

::

    $ py.test -v tests


Mailing List
------------

https://groups.google.com/forum/#!forum/aio-libs


Requirements
------------
* Python_ 3.5+
* aiohttp_
* botocore_

.. _Python: https://www.python.org
.. _asyncio: http://docs.python.org/3.5/library/asyncio.html
.. _botocore: https://github.com/boto/botocore
.. _aiohttp: https://github.com/KeepSafe/aiohttp


awscli
------

awscli depends on a single version of botocore, however aiobotocore only supports a
specific range of botocore versions. To ensure you install the latest version of
awscli that your specific combination or aiobotocore and botocore can support use::

    pip install -U aiobotocore[awscli]
