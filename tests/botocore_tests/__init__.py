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
import unittest
from unittest import mock  # noqa: F401

import botocore.loaders
from botocore.compat import HAS_CRT, parse_qs, urlparse

import aiobotocore.session
from aiobotocore.awsrequest import AioAWSResponse

_LOADER = botocore.loaders.Loader()


def requires_crt(reason=None):
    if reason is None:
        reason = "Test requires awscrt to be installed"

    def decorator(func):
        return unittest.skipIf(not HAS_CRT, reason)(func)

    return decorator


def skip_if_crt(reason=None):
    if reason is None:
        reason = "Test requires awscrt to NOT be installed"

    def decorator(func):
        return unittest.skipIf(HAS_CRT, reason)(func)

    return decorator


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


class HTTPStubberException(Exception):
    pass


class BaseHTTPStubber:
    class AsyncFileWrapper:
        def __init__(self, body: bytes):
            self._body = body

        async def read(self):
            return self._body

    def __init__(self, obj_with_event_emitter, strict=True):
        self.reset()
        self._strict = strict
        self._obj_with_event_emitter = obj_with_event_emitter

    def reset(self):
        self.requests = []
        self.responses = []

    def add_response(
        self, url='https://example.com', status=200, headers=None, body=b''
    ):
        if headers is None:
            headers = {}

        response = AioAWSResponse(
            url, status, headers, self.AsyncFileWrapper(body)
        )
        self.responses.append(response)

    @property
    def _events(self):
        raise NotImplementedError('_events')

    def start(self):
        self._events.register('before-send', self)

    def stop(self):
        self._events.unregister('before-send', self)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

    def __call__(self, request, **kwargs):
        self.requests.append(request)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            else:
                return response
        elif self._strict:
            raise HTTPStubberException('Insufficient responses')
        else:
            return None


class ClientHTTPStubber(BaseHTTPStubber):
    @property
    def _events(self):
        return self._obj_with_event_emitter.meta.events


class SessionHTTPStubber(BaseHTTPStubber):
    @property
    def _events(self):
        return self._obj_with_event_emitter.get_component('event_emitter')


def patch_load_service_model(
    session, monkeypatch, service_model_json, ruleset_json
):
    def mock_load_service_model(service_name, type_name, api_version=None):
        if type_name == 'service-2':
            return service_model_json
        if type_name == 'endpoint-rule-set-1':
            return ruleset_json

    loader = session.get_component('data_loader')
    monkeypatch.setattr(loader, 'load_service_model', mock_load_service_model)
