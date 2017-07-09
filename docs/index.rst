.. aiobotocore documentation master file, created by
   sphinx-quickstart on Sun Dec 11 17:08:38 2016.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

aiobotocore's documentation!
===========================
.. image:: https://travis-ci.org/aio-libs/aiobotocore.svg?branch=master
    :target: https://travis-ci.org/aio-libs/aiobotocore
.. image:: https://codecov.io/gh/aio-libs/aiobotocore/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/aio-libs/aiobotocore
.. image:: https://img.shields.io/pypi/v/aiobotocore.svg
    :target: https://pypi.python.org/pypi/aiobotocore

Async client for amazon services using botocore_ and aiohttp_/asyncio_.

Main purpose of this library to support amazon s3 api, but other services
should work (may be with minor fixes). For now we have tested
only upload/download api for S3, other users report that SQS and Dynamo
services work also. More tests coming soon.


Features
--------
 * Full async support for AWS services with botocore.
 * Library used in production with S3, SQS and Dynamo services


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
   api
   contributing


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`


.. _PEP492: https://www.python.org/dev/peps/pep-0492/
.. _Python: https://www.python.org
.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
.. _botocore: https://github.com/boto/botocore
.. _aiohttp: https://github.com/KeepSafe/aiohttp
