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

  6. Correct message for ``commit``. You need read (`Commit message format`_).

  7. Correct name for ``branch``. You need read (`Branch name format`_).

  8. Make pull request from github page for your branch against master branch

  9. Optionally make backport pull Request(s) for landing a bug fix
     into released aiobotocore versions.

  10. Add changes in file ``CHANGES.txt`` for this read (`Changelog update`_).


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

Commit message format
---------------------

   1. Commit prefixes:

      * **ISSUES-NUMBER** - *NUMBER* this number issues in github
      * **NOTISSUES**
      * **RELEASE-X.X.X**

   2. After the prefix comes the separator ``:`` and one space

   3. Next should be a brief description of your changes.


Examples:

.. code-block:: shell

   $ git commit -m "ISSUES-999: Update botocore till version x.x.x"
   $ git commit -m "ISSUES-999: Add changes summary in CHANGES.rst"
   $ git commit -m "NOTISSUES: Add example SNS"
   $ git commit -m "RELEASE-0.2.0: Removed support python < 3.5 look CHANGES"


Branch name format
------------------

   1. **ISSUES-NUMBER** - *NUMBER* this number issues in github

   2. **NOTISSUES-SUMMARY** - *SUMMARY* - your short name

   3. **RELEASE-X.X.X**

Examples:

.. code-block:: shell

   $ git checkout -b ISSUES-999
   $ git checkout -b ISSUES-9999
   $ git checkout -b NOTISSUES-ROLLBACK-3-LAST_COMMIT
   $ git checkout -b RELEASE-0.0.1


Changelog update
----------------

The ``CHANGES.rst`` contains information on changes.

.. note::

   If you know the release version and want to add a new commit before released out

   Example::

      0.1.0 (2018-09-01)
      ^^^^^^^^^^^^^^^^^^

      * Release & addition of changes file (Release-1.1.0)
      * Name commit (`ISSUES-99999 <https://github.com/aio-libs/aiobotocore/issues/99999>`_)
      * Name commit (NOTISSUES)

   If you set the date and version, it will be the last and will be released
   Version must be raised in your last committee

.. note::

   If you have a delayed release, just add your commit

   Example::

      X.X.X (YYYY-MM-DD)
      ^^^^^^^^^^^^^^^^^^

      * Release & addition of changes file (Release-1.1.0)
      * Name commit (`ISSUES-99999 <https://github.com/aio-libs/aiobotocore/issues/99999>`_)
      * Name commit (NOTISSUES)


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
