Contributing
============

Running Tests
-------------

.. _GitHub: https://github.com/aio-libs/aiobotocore

Thanks for your interest in contributing to ``aiobotocore``, there are multiple
ways and places you can contribute.

Fist of all just clone repository::

    $ git clone git@github.com:aio-libs/aiobotocore.git

Create virtualenv with at least python3.5 (older version are not supported).
For example using *virtualenvwrapper* commands could look like::

   $ cd aiobotocore
   $ mkvirtualenv --python=`which python3.5` aiobotocore


After that please install libraries required for development::

    $ pip install -r requirements-dev.txt
    $ pip install -e .

Congratulations, you are ready to run the test suite::

    $ make cov

To run individual use following command::

    $ py.test -sv tests/test_monitor.py -k test_name


Reporting an Issue
------------------
If you have found issue with `aiobotocore` please do
not hesitate to file an issue on the GitHub_ project. When filing your
issue please make sure you can express the issue with a reproducible test
case.

When reporting an issue we also need as much information about your environment
that you can include. We never know what information will be pertinent when
trying narrow down the issue. Please include at least the following
information:

* Version of `aiobotocore` and `python`.
* Version fo `botocore`.
* Platform you're running on (OS X, Linux).


Background and Implementation
-----------------------------
aiobotocore adds async functionality to botocore by replacing certain critical
methods in botocore classes with async versions.  The best way to see how this
works is by working backwards from `AioEndpoint._request`.  Because of this tight
integration aiobotocore is typically version locked to a particular release of
botocore.

How to Upgrade Botocore
-----------------------
aiobotocore's file names try to match the botocore files they functionally match.
For the most part botocore classes are sub-classed with the majority of the
botocore calls eventually called...however certain methods like
`PageIterator.next_page` had to be re-implemented so watch for changes in those
types of methods.

The best way I've seen to upgrade botocore support is by downloading the sources
of the release of botocore you're trying to upgrade to, and the version
of botocore that aiobotocore is currently locked to and do a folder based file
comparison (tools like DiffMerge are nice). You can then manually apply the
relevant changes to their aiobotocore equivalent(s). In order to support a range
of versions one would need validate the version each change was introduced and
select the newest of these to the current version.  This is further complicated
by the aiobotocore "extras" requirements which need to be updated to the
versions that are compatible with the above changes.

Notable changes we've seen in the past:

* new parameters added
* classes being moved to new files
* bodies of methods being updated

basically your typical code refactoring :)

NOTE: we've added hashes of the methods we replace in test_patches.py so if a
      aiohttp/botocore method changes that we depend on the test should fail.

The Future
----------
The long term goal is that botocore will implement async functionality directly.
See botocore issue: https://github.com/boto/botocore/issues/458  for details,
tracked in aiobotocore here: https://github.com/aio-libs/aiobotocore/issues/36
