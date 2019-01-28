Contributing
============

Instructions for contributors
-----------------------------

In order to make a clone of the `GitHub <https://github.com/aio-libs/aiobotocore>`_ repo: open the link and press the
"Fork" button on the upper-right menu of the web page.

I hope everybody knows how to work with git and gitlab nowadays :)

Workflow is pretty straightforward:

  1. Clone the `GitHub <https://github.com/aio-libs/aiobotocore>`_ repo

  2. Make a change

  3. Make sure all tests passed

  4. Make sure the coverage doesn't get worse.

  5. Commit changes to own aiobotocore

  6. Make pull request from github page for your branch against master branch

  7. Add changes in file ``CHANGES.txt`` for this read (`Changelog update`_).


Preconditions for running aiobotocore test suite
------------------------------------------------

We expect you to use a python virtual environment to run our tests.

There are several ways to make a virtual environment.

If you like to use *virtualenv* please run:

.. code-block:: shell

   $ cd aiobotocore
   $ virtualenv -p /usr/bin/python3 venv-aiobotocore
   $ . venv-aiobotocore/bin/activate

For standard python *venv-aiobotocore*:

.. code-block:: shell

   $ cd aiobotocore
   $ python3 -m venv venv-aiobotocore
   $ . venv-aiobotocore/bin/activate

For *virtualenvwrapper*:

.. code-block:: shell

   $ cd aiobotocore
   $ mkvirtualenv -p /usr/bin/python3 venv-aiobotocore
   $ workon venv-aiobotocore

There are other tools like *pyvenv* but you know the rule of thumb
now: create a python3 virtual environment and activate it.

After that please install libraries required for development:

.. code-block:: shell

   $ pip install -r requirements/dev.txt

Congratulations, you are ready to run the test suite!

Run aiobotocore test suite
--------------------------

After all the preconditions are met you can run tests typing the next
command:

.. code-block:: shell

   $ make test

Run aiobotocore flake suite
---------------------------

We try our best to write code according to pep, it will allow you to check yourself.

.. code-block:: shell

   $ make flake

Run aiobotocore coverage suite
------------------------------

We are trying hard to have good test coverage; please don't make it worse.

.. code-block:: shell

   $ make cov

.. note::

  If you want to use AWS within the test suite, you need create config ``~/.aws/credentials`` or the ``environment variable``

  Then the test run will look like this:
  .. code-block:: shell

     $ make aws-cov
     $ make aws-test


Documentation
-------------

We encourage documentation improvements.

Please before making a Pull Request about documentation changes run:

.. code-block:: shell

   $ make doc

Once it finishes it will output the index html page
``open file://`pwd`/docs/_build/html/index.html``.

Go to the link and make sure your doc changes looks good.

Changelog update
----------------

The ``CHANGES.rst`` contains information on changes.

.. note::

   If you know the release version and want to add a new commit before released out

   Example::

      0.1.0 (2018-09-01)
      ^^^^^^^^^^^^^^^^^^

      * Release & addition of changes file (Release-1.1.0)
      * Name commit
      * Name commit

   If you set the date and version, it will be the last and will be released
   Version must be raised in your last committee

.. note::

   If you have a delayed release, just add your commit

   Example::

      X.X.X (YYYY-MM-DD)
      ^^^^^^^^^^^^^^^^^^

      * Name commit
      * Name commit


   ``X.X.X (YYYY-MM-DD)`` - may be in the master, but should not get in the tag


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

See next section describing types of changes we must validate and support.

Hashes of Botocore Code (important)
-----------------------
Because of the way aiobotocore is implemented (see Background section), it is very tightly coupled with botocore.  The validity of these couplings are enforced in test_patches.py.  We also depend on some private properties in aiohttp, and because of this have entries in test_patches.py for this too.

These patches are important to catch cases where botocore functionality was added/removed and needs to be reflected in our overridden methods.  Changes include:

* parameters to methods added/removed
* classes/methods being moved to new files
* bodies of overridden methods updated

To ensure we catch and reflect this changes in aiobotocore, the test_patches.py file has the hashes of the parts of botocore we need to manually validate changes in.

test_patches.py file needs to be updated in two scenarios:

1. You're bumping the supported botocore/aiohttp version. In this case a failure in test_patches.py means you need to validate the section of code in aiohttp/botocore that no longer matches the hash in test_patches.py to see if any changes need to be reflected in aiobotocore which overloads, on depends on the code which triggered the hash mismatch.  This could there are new parameters we weren't expecting, parameters that are no longer passed to said overriden function(s), or an overridden function which calls a modified botocore method.  If this is a whole class collision the checks will be more extensive.
2. You're implementing missing aiobotocore functionality, in which case you need to add entries for all the methods in botocore/aiohttp which you are overriding or depending on private functionality.  For special cases, like when private attributes are used, you may have to hash the whole class so you can catch any case where the private property is used/updated to ensure it matches our expectations.

After you've validated the changes, you can update the hash in test_patches.py.

One would think we could just write enough unittests to catch all cases, however, this is impossible for two reasons:

1. We do not support all botocore unittests, for future work see discussion: https://github.com/aio-libs/aiobotocore/issues/213
2. Even if we did all the unittests from 1, we would not support NEW functionality added, unless we automatically pulled all new unittests as well from botocore.

Until we can perform ALL unittests from new releases of botocore, we are stuck with the patches.


The Future
----------
The long term goal is that botocore will implement async functionality directly.
See botocore issue: https://github.com/boto/botocore/issues/458  for details,
tracked in aiobotocore here: https://github.com/aio-libs/aiobotocore/issues/36
