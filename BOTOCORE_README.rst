botocore
========


Background and Implementation
-------------
aiobotocore adds async functionality to botocore by replacing certain critical
methods in botocore classes with async versions.  The best way to see how this
works is by working backwards from `AioEndpoint._request`.  Because of this tight
integration aiobotocore is typically version locked to a particular release of
botocore.

How to Upgrade Botocore
-------------
aiobotocore's file names try to match the botocore files they functionally match.
For the most part botocore classes are sub-classed with the majority of the
botocore calls eventually called...however certain methods like
`PageIterator.next_page` had to be re-implemented so watch for changes in those
types of methods.

The best way I've seen to upgrade botocore support is by downloading the sources
of the release of botocore you're trying to upgrade to, and the version
of botocore that aiobotocore is currently locked to and do a folder based file
comparison (tools like DiffMerge are nice). You can then manually apply the
relevant changes to their aiobotocore equivalent(s).

Notable changes we've seen in the past:

* new parameters added
* classes being moved to new files
* bodies of methods being updated

basically your typical code refactoring :)

NOTE: we've added hashes of the methods we replace in test_patches.py so if a
      aiohttp/botocore method changes that we depend on the test should fail.

The Future
-------------
The long term goal is that botocore will implement async functionality directly.
See botocore issue: https://github.com/boto/botocore/issues/458  for details,
tracked in aiobotocore here: https://github.com/aio-libs/aiobotocore/issues/36