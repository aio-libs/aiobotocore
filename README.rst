aio-botocore
============

The sole purpose of this fork is to release a version of
[aiobotocore](https://github.com/aio-libs/aiobotocore) with
relaxed constraints on the dependencies for botocore and boto3.
This should enable algorithms for resolving dependencies to
work more efficiently with this library.

Hopefully any risks in relaxing the versions allowed for
botocore and boto3 are minimal.  However, use at your own
risk (i.e. use your own unit tests and test coverage to
manage your risks).

If the original library works for your purposes, use it
instead of this library.  If changes to this library are working,
some form of the changes might get integrated into the original
project.  If so, hopefully this library will cease to exist
(or at least cease to be maintained in this form).

Install
-------
::

    $ pip install aio-botocore

The original library is installed using

    $ pip install aiobotocore
