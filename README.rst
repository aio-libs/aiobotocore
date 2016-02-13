aiobotocore
===========
.. image:: https://travis-ci.org/aio-libs/aiobotocore.svg?branch=master
    :target: https://travis-ci.org/aio-libs/aiobotocore

Async client for amazon services using botocore_ and aiohttp_/asyncio_.

Main purpose of this library to support amazon s3 api, but other services
should work (but may be with minor fixes). For now we have tested
only upload/download api for s3. More tests coming soon.


Install
-------
::

    $ pip install -e git+https://github.com/aio-libs/aiobotocore.git@master#egg=aiobotocore


Basic Example
-------------

.. code:: python

    import asyncio
    import aiobotocore

    AWS_ACCESS_KEY_ID = "xxx"
    AWS_SECRET_ACCESS_KEY = "xxx"


    @asyncio.coroutine
    def go(loop):

        bucket = 'dataintake'
        filename = 'dummy.bin'
        folder = 'aiobotocore'
        key = '{}/{}'.format(folder, filename)

        session = aiobotocore.get_session(loop=loop)
        client = session.create_client('s3', region_name='us-west-2',
                                       aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                                       aws_access_key_id=AWS_ACCESS_KEY_ID)
        # upload object to amazon s3
        data = b'\x01'*1024
        resp = yield from client.put_object(Bucket=bucket,
                                            Key=key,
                                            Body=data)
        print(resp)

        # getting s3 object properties of file we just uploaded
        resp = yield from client.get_object_acl(Bucket=bucket, Key=key)
        print(resp)

        # delete object from s3
        resp = yield from client.delete_object(Bucket=bucket, Key=key)
        print(resp)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(go(loop))


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


Requirements
------------

* Python_ 3.3+
* asyncio_ or Python_ 3.4+
* aiohttp_
* botocore_

.. _Python: https://www.python.org
.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
.. _botocore: https://github.com/boto/botocore
.. _aiohttp: https://github.com/KeepSafe/aiohttp
