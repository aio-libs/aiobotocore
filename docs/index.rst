.. aiobotocore documentation master file, created by
   sphinx-quickstart on Sun Dec 11 17:08:38 2016.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

aiobotocore's documentation!
============================
.. image:: https://travis-ci.org/aio-libs/aiobotocore.svg?branch=master
    :target: https://travis-ci.org/aio-libs/aiobotocore
.. image:: https://codecov.io/gh/aio-libs/aiobotocore/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/aio-libs/aiobotocore
.. image:: https://img.shields.io/pypi/v/aiobotocore.svg
    :target: https://pypi.python.org/pypi/aiobotocore

Async client for amazon services using botocore_ and aiohttp_/asyncio_.

Main purpose of this library to support amazon S3 API, but other services
should work (may be with minor fixes). For now we have tested
only upload/download API for S3, other users report that SQS and Dynamo
services work also. More tests coming soon.


Features
--------
 * Full async support for AWS services with botocore.
 * Library used in production with S3, SQS and Dynamo services


Basic Example
-------------

.. code:: python

    import asyncio
    import aiobotocore

    AWS_ACCESS_KEY_ID = "xxx"
    AWS_SECRET_ACCESS_KEY = "xxx"


    async def go():
        bucket = 'dataintake'
        filename = 'dummy.bin'
        folder = 'aiobotocore'
        key = '{}/{}'.format(folder, filename)

        session = aiobotocore.get_session()
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
    loop.run_until_complete(go())


awscli
------

awscli depends on a single version of botocore, however aiobotocore only supports a
specific range of botocore versions. To ensure you install the latest version of
awscli that your specific combination or aiobotocore and botocore can support use::

    pip install -U aiobotocore[awscli]

Contents
--------

.. toctree::
   :maxdepth: 2

   tutorial
   examples
   contributing


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`


.. _PEP492: https://www.python.org/dev/peps/pep-0492/
.. _Python: https://www.python.org
.. _asyncio: http://docs.python.org/3.5/library/asyncio.html
.. _botocore: https://github.com/boto/botocore
.. _aiohttp: https://github.com/KeepSafe/aiohttp
