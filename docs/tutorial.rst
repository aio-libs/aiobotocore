Getting Started With aiobotocore
================================

Following tutorial based on `botocore tutorial <http://botocore.readthedocs.io/en/latest/tutorial/index.html>`_.

The ``aiobotocore`` package provides a low-level interface to Amazon
services.  It is responsible for:

* Providing access to all available services
* Providing access to all operations within a service
* Marshaling all parameters for a particular operation in the correct format
* Signing the request with the correct authentication signature
* Receiving the response and returning the data in native Python data structures

``aiobotocore`` does not provide higher-level abstractions on top of these
services, operations and responses.  That is left to the application
layer.  The goal of ``aiobotocore`` is to handle all of the low-level details
of making requests and getting results from a service.

The ``aiobotocore`` package is mainly data-driven.  Each service has a JSON
description which specifies all of the operations the service supports,
all of the parameters the operation accepts, all of the documentation
related to the service, information about supported regions and endpoints, etc.
Because this data can be updated quickly based on the canonical description
of these services, it's much easier to keep ``aiobotocore`` current.

Using Botocore
==============

The first step in using aiobotocore is to create a ``Session`` object.
``Session`` objects then allow you to create individual clients::

    session = aiobotocore.get_session()
    async with session.create_client('s3', region_name='us-west-2',
                                     aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                                     aws_access_key_id=AWS_ACCESS_KEY_ID) as client:

Once you have that client created, each operation provided by the service is
mapped to a method.  Each method takes ``**kwargs`` that maps to the parameter
names exposed by the service.  For example, using the ``client`` object created
above::

    # upload object to amazon s3
    data = b'\x01'*1024
    resp = await client.put_object(Bucket=bucket,
                                   Key=key, Body=data)
    print(resp)

    # getting s3 object properties of file we just uploaded
    resp = await client.get_object_acl(Bucket=bucket, Key=key)
    print(resp)

    # delete object from s3
    resp = await client.delete_object(Bucket=bucket, Key=key)
    print(resp)
