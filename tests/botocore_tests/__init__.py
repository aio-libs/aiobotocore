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

import binascii
import contextlib
import os
import random
import shutil
import tempfile
import time
from unittest import mock  # noqa: F401

import botocore.loaders
from botocore.compat import parse_qs, urlparse

import aiobotocore.session

_LOADER = botocore.loaders.Loader()


def random_chars(num_chars):
    """Returns random hex characters.

    Useful for creating resources with random names.

    """
    return binascii.hexlify(os.urandom(int(num_chars / 2))).decode('ascii')


def create_session(**kwargs):
    # Create a Session object.  By default,
    # the _LOADER object is used as the loader
    # so that we reused the same models across tests.
    session = aiobotocore.session.AioSession(**kwargs)
    session.register_component('data_loader', _LOADER)
    session.set_config_variable('credentials_file', 'noexist/foo/botocore')
    return session


@contextlib.contextmanager
def temporary_file(mode):
    """This is a cross platform temporary file creation.

    tempfile.NamedTemporary file on windows creates a secure temp file
    that can't be read by other processes and can't be opened a second time.

    For tests, we generally *want* them to be read multiple times.
    The test fixture writes the temp file contents, the test reads the
    temp file.

    """
    temporary_directory = tempfile.mkdtemp()
    basename = f'tmpfile-{int(time.time())}-{random.randint(1, 1000)}'
    full_filename = os.path.join(temporary_directory, basename)
    open(full_filename, 'w').close()
    try:
        with open(full_filename, mode) as f:
            yield f
    finally:
        shutil.rmtree(temporary_directory)


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
