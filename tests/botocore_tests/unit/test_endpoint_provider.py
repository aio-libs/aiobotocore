# Copyright 2012-2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
from unittest.mock import patch

import pytest

from aiobotocore.regions import AioEndpointRulesetResolver

REGION_TEMPLATE = "{Region}"
REGION_REF = {"ref": "Region"}
BUCKET_ARN_REF = {"ref": "bucketArn"}
PARSE_ARN_FUNC = {
    "fn": "aws.parseArn",
    "argv": [{"ref": "Bucket"}],
    "assign": "bucketArn",
}
STRING_EQUALS_FUNC = {
    "fn": "stringEquals",
    "argv": [
        {
            "fn": "getAttr",
            "argv": [BUCKET_ARN_REF, "region"],
            "assign": "bucketRegion",
        },
        "",
    ],
}
DNS_SUFFIX_TEMPLATE = "{PartitionResults#dnsSuffix}"
URL_TEMPLATE = (
    f"https://{REGION_TEMPLATE}.myGreatService.{DNS_SUFFIX_TEMPLATE}"
)
ENDPOINT_AUTH_SCHEMES_DICT = {
    "url": URL_TEMPLATE,
    "properties": {
        "authSchemes": [
            {
                "disableDoubleEncoding": True,
                "name": "foo",
                "signingName": "s3-outposts",
                "signingRegionSet": ["*"],
            },
            {
                "disableDoubleEncoding": True,
                "name": "bar",
                "signingName": "s3-outposts",
                "signingRegion": REGION_TEMPLATE,
            },
        ],
    },
    "headers": {},
}


@pytest.mark.parametrize(
    "auth_scheme_preference,expected_auth_scheme_name",
    [
        (
            'foo,bar',
            'foo',
        ),
        (
            'bar,foo',
            'bar',
        ),
        (
            'xyz,foo,bar',
            'foo',
        ),
    ],
)
def test_auth_scheme_preference(
    auth_scheme_preference, expected_auth_scheme_name, monkeypatch
):
    conditions = (
        [
            PARSE_ARN_FUNC,
            {
                "fn": "not",
                "argv": [STRING_EQUALS_FUNC],
            },
            {
                "fn": "aws.partition",
                "argv": [REGION_REF],
                "assign": "PartitionResults",
            },
        ],
    )
    resolver = AioEndpointRulesetResolver(
        endpoint_ruleset_data={
            'version': '1.0',
            'parameters': {},
            'rules': [
                {
                    'conditions': conditions,
                    'type': 'endpoint',
                    'endpoint': ENDPOINT_AUTH_SCHEMES_DICT,
                }
            ],
        },
        partition_data={},
        service_model=None,
        builtins={},
        client_context=None,
        event_emitter=None,
        use_ssl=True,
        requested_auth_scheme=None,
        auth_scheme_preference=auth_scheme_preference,
    )
    auth_schemes = [
        {'name': 'foo', 'signingName': 's3', 'signingRegion': 'ap-south-1'},
        {'name': 'bar', 'signingName': 's3', 'signingRegion': 'ap-south-2'},
    ]
    with (
        patch.dict(
            'botocore.auth.AUTH_TYPE_MAPS',
            {'bar': None, 'foo': None},
            clear=True,
        ),
        patch.dict(
            'botocore.auth.AUTH_PREF_TO_SIGNATURE_VERSION',
            {'bar': 'bar', 'foo': 'foo'},
            clear=True,
        ),
    ):
        name, scheme = resolver.auth_schemes_to_signing_ctx(auth_schemes)
    assert name == expected_auth_scheme_name
