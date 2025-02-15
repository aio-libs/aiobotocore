# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

from botocore.compat import parse_qs, urlparse


def _urlparse(url):
    if isinstance(url, bytes):
        # Not really necessary, but it helps to reduce noise on Python 2.x
        url = url.decode('utf8')
    return urlparse(url)


def assert_url_equal(url1, url2):
    parts1 = _urlparse(url1)
    parts2 = _urlparse(url2)

    # Because the query string ordering isn't relevant, we have to parse
    # every single part manually and then handle the query string.
    assert parts1.scheme == parts2.scheme
    assert parts1.netloc == parts2.netloc
    assert parts1.path == parts2.path
    assert parts1.params == parts2.params
    assert parts1.fragment == parts2.fragment
    assert parts1.username == parts2.username
    assert parts1.password == parts2.password
    assert parts1.hostname == parts2.hostname
    assert parts1.port == parts2.port
    assert parse_qs(parts1.query) == parse_qs(parts2.query)
