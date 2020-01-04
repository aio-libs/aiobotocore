Changes
-------

0.11.1 (2020-01-03)
^^^^^^^^^^^^^^^^^^^
* Fixed event streaming API calls like S3 Select.

0.11.0 (2019-11-12)
^^^^^^^^^^^^^^^^^^^
* replace CaseInsensitiveDict with urllib3 equivalent #744
  (thanks to inspiration from @craigmccarter and @kevchentw)
* bump botocore to 1.13.14
* fix for mismatched botocore method replacements

0.10.4 (2019-10-24)
^^^^^^^^^^^^^^^^^^^
* Make AioBaseClient.close method async #724 (thanks @bsitruk)
* Bump awscli, boto3, botocore #735 (thanks @bbrendon)
* switch paginator to async_generator, add result_key_iters
  (deprecate next_page method)

0.10.3 (2019-07-17)
^^^^^^^^^^^^^^^^^^^
* Bump botocore and extras

0.10.2 (2019-02-11)
^^^^^^^^^^^^^^^^^^^
* Fix response-received emitted event #682

0.10.1 (2019-02-08)
^^^^^^^^^^^^^^^^^^^
* Make tests pass with pytest 4.1 #669 (thanks @yan12125)
* Support Python 3.7 #671 (thanks to @yan12125)
* Update RTD build config #672 (thanks @willingc)
* Bump to botocore 1.12.91 #679

0.10.0 (2018-12-09)
^^^^^^^^^^^^^^^^^^^
* Update to botocore 1.12.49 #639 (thanks @terrycain)

0.9.4 (2018-08-08)
^^^^^^^^^^^^^^^^^^
* Add ClientPayloadError as retryable exception

0.9.3 (2018-07-16)
^^^^^^^^^^^^^^^^^^
* Bring botocore up to date

0.9.2 (2018-05-05)
^^^^^^^^^^^^^^^^^^
* bump aiohttp requirement to fix read timeouts

0.9.1 (2018-05-04)
^^^^^^^^^^^^^^^^^^
* fix timeout bug introduced in last release

0.9.0 (2018-06-01)
^^^^^^^^^^^^^^^^^^
* bump aiohttp to 3.3.x
* remove unneeded set_socket_timeout

0.8.0 (2018-05-07)
^^^^^^^^^^^^^^^^^^
* Fix pagination #573 (thanks @adamrothman)
* Enabled several s3 tests via moto
* Bring botocore up to date

0.7.0 (2018-05-01)
^^^^^^^^^^^^^^^^^^
* Just version bump

0.6.1a0 (2018-05-01)
^^^^^^^^^^^^^^^^^^^^
* bump to aiohttp 3.1.x
* switch tests to Python 3.5+
* switch to native coroutines
* fix non-streaming body timeout retries

0.6.0 (2018-03-04)
^^^^^^^^^^^^^^^^^^
* Upgrade to aiohttp>=3.0.0 #536 (thanks @Gr1N)

0.5.3 (2018-02-23)
^^^^^^^^^^^^^^^^^^
* Fixed waiters #523 (thanks @dalazx)
* fix conn_timeout #485

0.5.2 (2017-12-06)
^^^^^^^^^^^^^^^^^^
* Updated awscli dependency #461

0.5.1 (2017-11-10)
^^^^^^^^^^^^^^^^^^
* Disabled compressed response #430

0.5.0 (2017-11-10)
^^^^^^^^^^^^^^^^^^
* Fix error botocore error checking #190
* Update supported botocore requirement to: >=1.7.28, <=1.7.40
* Bump aiohttp requirement to support compressed responses correctly #298

0.4.5 (2017-09-05)
^^^^^^^^^^^^^^^^^^
* Added SQS examples and tests #336
* Changed requirements.txt structure #336
* bump to botocore 1.7.4
* Added DynamoDB examples and tests #340


0.4.4 (2017-08-16)
^^^^^^^^^^^^^^^^^^
* add the supported versions of boto3 to extras require #324

0.4.3 (2017-07-05)
^^^^^^^^^^^^^^^^^^
* add the supported versions of awscli to extras require #273 (thanks @graingert)

0.4.2 (2017-07-03)
^^^^^^^^^^^^^^^^^^
* update supported aiohttp requirement to: >=2.0.4, <=2.3.0
* update supported botocore requirement to: >=1.5.71, <=1.5.78

0.4.1 (2017-06-27)
^^^^^^^^^^^^^^^^^^
* fix redirects #268

0.4.0 (2017-06-19)
^^^^^^^^^^^^^^^^^^
* update botocore requirement to: botocore>=1.5.34, <=1.5.70
* fix read_timeout due to #245
* implement set_socket_timeout

0.3.3 (2017-05-22)
^^^^^^^^^^^^^^^^^^
* switch to PEP 440 version parser to support 'dev' versions

0.3.2 (2017-05-22)
^^^^^^^^^^^^^^^^^^
* Fix botocore integration
* Provisional fix for aiohttp 2.x stream support
* update botocore requirement to: botocore>=1.5.34, <=1.5.52

0.3.1 (2017-04-18)
^^^^^^^^^^^^^^^^^^
* Fixed Waiter support

0.3.0 (2017-04-01)
^^^^^^^^^^^^^^^^^^
* Added support for aiohttp>=2.0.4 (thanks @achimnol)
* update botocore requirement to: botocore>=1.5.0, <=1.5.33

0.2.3 (2017-03-22)
^^^^^^^^^^^^^^^^^^
* update botocore requirement to: botocore>=1.5.0, <1.5.29

0.2.2 (2017-03-07)
^^^^^^^^^^^^^^^^^^
* set aiobotocore.__all__ for * imports #121 (thanks @graingert)
* fix ETag in head_object response #132

0.2.1 (2017-02-01)
^^^^^^^^^^^^^^^^^^
* Normalize headers and handle redirection by botocore #115 (thanks @Fedorof)

0.2.0 (2017-01-30)
^^^^^^^^^^^^^^^^^^
* add support for proxies (thanks @jjonek)
* remove AioConfig verify_ssl connector_arg as this is handled by the
  create_client verify param
* remove AioConfig limit connector_arg as this is now handled by
  by the Config `max_pool_connections` property (note default is 10)

0.1.1 (2017-01-16)
^^^^^^^^^^^^^^^^^^
* botocore updated to version 1.5.0

0.1.0 (2017-01-12)
^^^^^^^^^^^^^^^^^^
* Pass timeout to aiohttp.request to enforce read_timeout #86 (thanks @vharitonsky)
  (bumped up to next semantic version due to read_timeout enabling change)

0.0.6 (2016-11-19)
^^^^^^^^^^^^^^^^^^

* Added enforcement of plain response #57 (thanks @rymir)
* botocore updated to version 1.4.73 #74 (thanks @vas3k)


0.0.5 (2016-06-01)
^^^^^^^^^^^^^^^^^^

* Initial alpha release
