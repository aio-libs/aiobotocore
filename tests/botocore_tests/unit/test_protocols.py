#!/usr/bin/env python
# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
"""Test runner for the JSON models compliance tests

This is a test runner for all the output JSON tests defined in
``tests/unit/protocols/``.

You can use the normal ``python -m pytest tests/unit/test_protocols.py``
to run this test.  In addition, there are several env vars you can use during
development.

Tests are broken down by filename, test suite, testcase.  When a test fails
you'll see the protocol (filename), test suite, and test case number of the
failed test.

::

    Description           : Scalar members (0:0)  <--- (suite_id:test_id)
    Protocol:             : ec2                  <--- test file (ec2.json)
    Given                 : ...
    Response              : ...
    Expected serialization: ...
    Actual serialization  : ...
    Assertion message     : ...

To run tests from only a single file, you can set the
BOTOCORE_TEST env var::

    BOTOCORE_TEST=tests/unit/protocols/input/json.json pytest tests/unit/test_protocols.py

To run a single test suite you can set the BOTOCORE_TEST_ID env var:

    BOTOCORE_TEST=tests/unit/protocols/input/json.json BOTOCORE_TEST_ID=5 \
        pytest tests/unit/test_protocols.py

To run a single test case in a suite (useful when debugging a single test), you
can set the BOTOCORE_TEST_ID env var with the ``suite_id:test_id`` syntax.

    BOTOCORE_TEST_ID=5:1 pytest tests/unit/test_protocols.py

"""

import base64
import copy
import os
from enum import Enum

import pytest
from botocore.awsrequest import HeadersDict
from botocore.compat import OrderedDict, json
from botocore.model import NoShapeFoundError, OperationModel, ServiceModel
from botocore.utils import parse_timestamp
from dateutil.tz import tzutc

from aiobotocore.eventstream import AioEventStream
from aiobotocore.parsers import (
    AioEC2QueryParser,
    AioJSONParser,
    AioQueryParser,
    AioRestJSONParser,
    AioRestXMLParser,
    AioRpcV2CBORParser,
)

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'protocols'
)
PROTOCOL_PARSERS = {
    'ec2': AioEC2QueryParser,
    'query': AioQueryParser,
    'json': AioJSONParser,
    'rest-json': AioRestJSONParser,
    'rest-xml': AioRestXMLParser,
    'smithy-rpc-v2-cbor': AioRpcV2CBORParser,
}
IGNORE_LIST_FILENAME = "protocol-tests-ignore-list.json"


class TestType(Enum):
    # Tell test runner to ignore this class
    __test__ = False

    INPUT = "input"
    OUTPUT = "output"


def get_protocol_test_ignore_list():
    ignore_list_path = os.path.join(TEST_DIR, IGNORE_LIST_FILENAME)
    with open(ignore_list_path) as f:
        return json.load(f)


def _compliance_tests(test_type=None):
    inp = test_type is None or test_type is TestType.INPUT
    out = test_type is None or test_type is TestType.OUTPUT

    for full_path in _walk_files():
        if full_path.endswith('.json'):
            for model, case, basename in _load_cases(full_path):
                protocol = basename.replace('.json', '')
                if _should_ignore_test(
                    protocol,
                    "input" if inp else "output",
                    model['description'],
                    case.get('id'),
                ):
                    continue
                if ('params' in case and inp) or ('response' in case and out):
                    yield model, case, basename


class FakeStreamReader:
    class ChunkedIterator:
        def __init__(self, chunks):
            self.iter = iter((chunks,))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                result = next(self.iter)
                return result, True
            except StopIteration:
                raise StopAsyncIteration()

    def __init__(self, chunks):
        self.chunks = base64.b64decode(chunks)
        self.content = self

    def iter_chunks(self):
        return self.ChunkedIterator(self.chunks)


@pytest.mark.parametrize(
    "json_description, case, basename", _compliance_tests(TestType.OUTPUT)
)
async def test_output_compliance(json_description, case, basename):
    service_description = copy.deepcopy(json_description)
    case = copy.deepcopy(case)
    operation_name = case.get('given', {}).get('name', 'OperationName')
    service_description['operations'] = {
        operation_name: case,
    }
    case['response']['context'] = {'operation_name': operation_name}
    try:
        model = ServiceModel(service_description)
        operation_model = OperationModel(case['given'], model)
        protocol = model.metadata['protocol']
        parser = PROTOCOL_PARSERS[protocol](
            timestamp_parser=_compliance_timestamp_parser
        )
        # We load the json as utf-8, but the response parser is at the
        # botocore boundary, so it expects to work with bytes.
        # If a test case doesn't define a response body, set it to `None`.
        if 'body' in case['response']:
            body_bytes = case['response']['body'].encode('utf-8')
            case['response']['body'] = body_bytes
        else:
            case['response']['body'] = (
                b'' if protocol != "query" else b'<xml/>'
            )
        # We need the headers to be case insensitive
        # If a test case doesn't define response headers, set it to an empty `HeadersDict`.
        case['response']['headers'] = HeadersDict(
            case['response'].get('headers', {})
        )
        # If this is an event stream fake the raw streamed response
        if operation_model.has_event_stream_output:
            case['response']['body'] = FakeStreamReader(body_bytes)
        if 'error' in case:
            output_shape = operation_model.output_shape
            if protocol == 'smithy-rpc-v2-cbor':
                case['response']['body'] = base64.b64decode(
                    case['response']['body']
                )
            parsed = await parser.parse(case['response'], output_shape)
            try:
                error_code = parsed.get("Error", {}).get("Code")
                error_shape = model.shape_for_error_code(error_code)
            except NoShapeFoundError:
                error_shape = None
            if error_shape is not None:
                error_parse = await parser.parse(case['response'], error_shape)
                parsed.update(error_parse)
        else:
            output_shape = operation_model.output_shape
            if protocol == 'query' and output_shape and output_shape.members:
                output_shape.serialization['resultWrapper'] = (
                    f'{operation_name}Result'
                )
            elif protocol == 'smithy-rpc-v2-cbor':
                case['response']['body'] = base64.b64decode(
                    case['response']['body']
                )
            parsed = await parser.parse(case['response'], output_shape)
        parsed = await _fixup_parsed_result(parsed)
    except Exception as e:  # pragma: no cover
        msg = (
            "\nFailed to run test  : {}\n"
            "Protocol            : {}\n"
            "Description         : {} ({}:{})\n".format(
                e,
                model.metadata['protocol'],
                case['description'],
                case['suite_id'],
                case['test_id'],
            )
        )
        raise AssertionError(msg)
    try:
        if 'error' in case:
            expected_result = {
                'Error': {
                    'Code': case.get('errorCode', ''),
                    'Message': case.get('errorMessage', ''),
                }
            }
            expected_result.update(case['error'])
        else:
            expected_result = case['result']
        assert_equal(parsed, expected_result, "Body")
    except Exception as e:  # pragma: no cover
        _output_failure_message(
            model.metadata['protocol'], case, parsed, expected_result, e
        )


async def _fixup_parsed_result(parsed):
    # This function contains all the transformation we need
    # to do from the response _our_ response parsers give
    # vs. the expected responses in the protocol tests.
    # These are implementation specific changes, not any
    # "we're not following the spec"-type changes.

    # 1. RequestMetadata.  We parse this onto the returned dict, but compliance
    # tests don't have any specs for how to deal with request metadata.
    if 'ResponseMetadata' in parsed:
        del parsed['ResponseMetadata']
    # 2. Binary blob types.  In the protocol test, blob types, when base64
    # decoded, always decode to something that can be expressed via utf-8.
    # This is not always the case.  In python3, the blob type is designed to
    # return a bytes (not str) object.  However, for these tests we'll work for
    # any bytes type, and decode it as utf-8 because we know that's safe for
    # the compliance tests.
    parsed = _convert_bytes_to_str(parsed)
    # 3. We need to expand the event stream object into the list of events
    for key, value in parsed.items():
        if isinstance(value, AioEventStream):
            parsed[key] = _convert_bytes_to_str([v async for v in value])
            break
    # 4. We parse the entire error body into the "Error" field for rest-xml
    # which causes some modeled fields in the response to be placed under the
    # error key. We don't have enough information in the test suite to assert
    # these properly, and they probably shouldn't be there in the first place.
    if 'Error' in parsed:
        error_keys = list(parsed['Error'].keys())
        for key in error_keys:
            if key not in ['Code', 'Message']:
                del parsed['Error'][key]
    # 5. Special float types. In the protocol test suite, certain special float
    # types are represented as strings: "Infinity", "-Infinity", and "NaN".
    # However, we parse these values as actual floats types, so we need to convert
    # them back to their string representation.
    parsed = _convert_special_floats_to_string(parsed)
    return parsed


def _convert_bytes_to_str(parsed):
    if isinstance(parsed, dict):
        new_dict = {}
        for key, value in parsed.items():
            new_dict[key] = _convert_bytes_to_str(value)
        return new_dict
    elif isinstance(parsed, bytes):
        return parsed.decode('utf-8')
    elif isinstance(parsed, list):
        new_list = []
        for item in parsed:
            new_list.append(_convert_bytes_to_str(item))
        return new_list
    else:
        return parsed


def _convert_special_floats_to_string(parsed):
    for key, value in parsed.items():
        if value == float('Infinity'):
            parsed[key] = 'Infinity'
        elif value == float('-Infinity'):
            parsed[key] = '-Infinity'
        elif value != value:
            parsed[key] = 'NaN'
    return parsed


def _compliance_timestamp_parser(value):
    datetime = parse_timestamp(value)
    # Convert from our time zone to UTC
    datetime = datetime.astimezone(tzutc())
    # Convert to epoch.
    return datetime.timestamp()


def _output_failure_message(
    protocol_type, case, actual_parsed, expected_result, error
):  # pragma: no cover
    j = _try_json_dump
    error_message = (
        "\nDescription           : {} ({}:{})\n"
        "Protocol:             : {}\n"
        "Given                 : {}\n"
        "Response              : {}\n"
        "Expected serialization: {}\n"
        "Actual serialization  : {}\n"
        "Assertion message     : {}\n".format(
            case['description'],
            case['suite_id'],
            case['test_id'],
            protocol_type,
            j(case['given']),
            j(case['response']),
            j(expected_result),
            j(actual_parsed),
            error,
        )
    )
    raise AssertionError(error_message)


def _try_json_dump(obj):  # pragma: no cover
    try:
        return json.dumps(obj)
    except (ValueError, TypeError):
        return str(obj)


def assert_equal(first, second, prefix):
    # A better assert equals.  It allows you to just provide
    # prefix instead of the entire message.
    try:
        assert first == second
    except Exception:  # pragma: no cover
        try:
            better = f"{prefix} (actual != expected)\n{json.dumps(first, indent=2)} !=\n{json.dumps(second, indent=2)}"
        except (ValueError, TypeError):
            better = f"{prefix} (actual != expected)\n{first} !=\n{second}"
        raise AssertionError(better)


def _walk_files():
    # Check for a shortcut when running the tests interactively.
    # If a BOTOCORE_TEST env var is defined, that file is used as the
    # only test to run.  Useful when doing feature development.
    single_file = os.environ.get('BOTOCORE_TEST')
    if single_file is not None:
        yield os.path.abspath(single_file)
    else:
        for root, _, filenames in os.walk(TEST_DIR):
            for filename in filenames:
                if filename == IGNORE_LIST_FILENAME:
                    continue
                yield os.path.join(root, filename)


def _load_cases(full_path):
    # During development, you can set the BOTOCORE_TEST_ID
    # to run a specific test suite or even a specific test case.
    # The format is BOTOCORE_TEST_ID=suite_id:test_id or
    # BOTOCORE_TEST_ID=suite_id
    suite_id, test_id = _get_suite_test_id()
    all_test_data = json.load(
        open(full_path, encoding='utf-8'), object_pairs_hook=OrderedDict
    )
    basename = os.path.basename(full_path)
    for i, test_data in enumerate(all_test_data):
        if suite_id is not None and i != suite_id:
            continue
        cases = test_data.pop('cases')
        description = test_data['description']
        for j, case in enumerate(cases):
            if test_id is not None and j != test_id:
                continue
            case['description'] = description
            case['suite_id'] = i
            case['test_id'] = j
            yield (test_data, case, basename)


def _get_suite_test_id():  # pragma: no cover
    if 'BOTOCORE_TEST_ID' not in os.environ:
        return None, None
    test_id = None
    suite_id = None
    split = os.environ['BOTOCORE_TEST_ID'].split(':')
    try:
        if len(split) == 2:
            suite_id, test_id = int(split[0]), int(split[1])
        else:
            suite_id = int(split[0])
    except TypeError:
        # Same exception, just give a better error message.
        raise TypeError(
            "Invalid format for BOTOCORE_TEST_ID, should be "
            "suite_id[:test_id], and both values should be "
            "integers."
        )
    return suite_id, test_id


def _should_ignore_test(protocol, test_type, suite, case):
    """
    Determines if a protocol test should be ignored.

    :type protocol: str
    :param protocol: The protocol name as represented by its corresponding
        protocol test file name (without the .json extension).

    :type test_type: str
    :param test_type: The protocol test type ("input" or "output").

    :type suite: str
    :param suite: The "description" attribute of a protocol test suite.

    :type case: str
    :param case: The "id" attribute of a specific protocol test case.

    :return: True if the protocol test should be ignored, False otherwise.
    :rtype: bool
    """
    # Get test suites and cases to ignore for all protocols.
    ignore_list = get_protocol_test_ignore_list()
    general_ignore_list = ignore_list.get('general', {}).get(test_type, {})
    general_suites = general_ignore_list.get('suites', [])
    general_cases = general_ignore_list.get('cases', [])
    if suite in general_suites or case in general_cases:
        return True

    # Get test suites and cases to ignore for a specific protocol.
    protocol_ignore_list = (
        ignore_list.get('protocols', {}).get(protocol, {}).get(test_type, {})
    )
    protocol_suites = protocol_ignore_list.get('suites', [])
    protocol_cases = protocol_ignore_list.get('cases', [])
    return suite in protocol_suites or case in protocol_cases
