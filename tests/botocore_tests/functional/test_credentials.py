# Copyright 2015 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from aiobotocore.session import AioSession
from tests.botocore_tests import (
    ClientHTTPStubber,
    SessionHTTPStubber,
)
from tests.botocore_tests.functional.test_useragent import (
    get_captured_ua_strings,
    parse_registered_feature_ids,
)


@pytest.mark.parametrize(
    "creds_env_var,creds_file_content,patches,expected_feature_id",
    [
        (
            'AWS_SHARED_CREDENTIALS_FILE',
            '[default]\naws_access_key_id = FAKEACCESSKEY\naws_secret_access_key = FAKESECRET',
            [
                patch(
                    "aiobotocore.credentials.AioAssumeRoleProvider.load",
                    return_value=None,
                ),
                patch(
                    "aiobotocore.credentials.AioEnvProvider.load",
                    return_value=None,
                ),
            ],
            'n',
        ),
        (
            'AWS_CONFIG_FILE',
            '[default]\naws_access_key_id = FAKEACCESSKEY\naws_secret_access_key = FAKESECRET',
            [
                patch(
                    "aiobotocore.credentials.AioAssumeRoleProvider.load",
                    return_value=None,
                ),
                patch(
                    "aiobotocore.credentials.AioEnvProvider.load",
                    return_value=None,
                ),
                patch(
                    "aiobotocore.credentials.AioSharedCredentialProvider.load",
                    return_value=None,
                ),
            ],
            'n',
        ),
    ],
)
async def test_user_agent_has_file_based_feature_ids(
    creds_env_var,
    creds_file_content,
    patches,
    expected_feature_id,
    tmp_path,
    monkeypatch,
):
    credentials_file = tmp_path / "creds"
    credentials_file.write_text(creds_file_content)
    monkeypatch.setenv(creds_env_var, str(credentials_file))

    for patch_obj in patches:
        patch_obj.start()

    try:
        session = AioSession()
        async with session.create_client(
            "s3", region_name="us-east-1"
        ) as client:
            await _assert_feature_ids_in_ua(client, expected_feature_id)
    finally:
        for patch_obj in patches:
            patch_obj.stop()


async def _assert_feature_ids_in_ua(client, expected_feature_ids):
    """Helper to test feature IDs appear in user agent for multiple calls."""
    with ClientHTTPStubber(client, strict=True) as http_stubber:
        http_stubber.add_response()
        http_stubber.add_response()
        await client.list_buckets()
        await client.list_buckets()

    ua_strings = get_captured_ua_strings(http_stubber)
    for ua_string in ua_strings:
        feature_list = parse_registered_feature_ids(ua_string)
        for expected_id in expected_feature_ids:
            assert expected_id in feature_list


@patch("aiobotocore.credentials.AioCachedCredentialFetcher._load_from_cache")
@patch("aiobotocore.credentials.AioSSOProvider._load_sso_config")
@patch(
    "aiobotocore.credentials.AioAssumeRoleWithWebIdentityProvider.load",
    return_value=None,
)
@patch("aiobotocore.credentials.AioAssumeRoleProvider.load", return_value=None)
@patch("aiobotocore.credentials.AioEnvProvider.load", return_value=None)
async def test_user_agent_has_sso_legacy_credentials_feature_id(
    _unused_mock_env_load,
    _unused_mock_shared_load,
    _unused_mock_config_load,
    mock_load_sso_config,
    mock_load_sso_credentials,
    monkeypatch,
    patched_session,
):
    fake_fetcher_kwargs = {
        'sso_start_url': "https://test.awsapps.com/start",
        'sso_region': "us-east-1",
        'sso_role_name': "Administrator",
        'sso_account_id': "1234567890",
    }
    fake_response = {
        "ProviderType": "sso",
        "Credentials": {
            "role_name": "FAKEROLE",
            "AccessKeyId": "FAKEACCESSKEY",
            "SecretAccessKey": "FAKESECRET",
            "SessionToken": "FAKETOKEN",
            "Expiration": "2099-01-01T00:00:00Z",
        },
    }

    mock_load_sso_config.return_value = fake_fetcher_kwargs
    async with patched_session.create_client(
        "s3", region_name="us-east-1"
    ) as client_one:
        mock_load_sso_credentials.return_value = fake_response

        await _assert_feature_ids_in_ua(client_one, ['t', 'u'])


@patch("aiobotocore.credentials.AioCachedCredentialFetcher._load_from_cache")
@patch("aiobotocore.credentials.AioSSOProvider._load_sso_config")
@patch(
    "aiobotocore.credentials.AioAssumeRoleWithWebIdentityProvider.load",
    return_value=None,
)
@patch("aiobotocore.credentials.AioAssumeRoleProvider.load", return_value=None)
@patch("aiobotocore.credentials.AioEnvProvider.load", return_value=None)
async def test_user_agent_has_sso_credentials_feature_id(
    _unused_mock_env_load,
    _unused_mock_shared_load,
    _unused_mock_config_load,
    mock_load_sso_config,
    mock_load_sso_credentials,
    monkeypatch,
    patched_session,
):
    fake_fetcher_kwargs = {
        'sso_session': 'sample_test',
        'sso_start_url': "https://test.awsapps.com/start",
        'sso_region': "us-east-1",
        'sso_role_name': "Administrator",
        'sso_account_id': "1234567890",
    }
    fake_response = {
        "ProviderType": "sso",
        "Credentials": {
            "role_name": "FAKEROLE",
            "AccessKeyId": "FAKEACCESSKEY",
            "SecretAccessKey": "FAKESECRET",
            "SessionToken": "FAKETOKEN",
            "Expiration": "2099-01-01T00:00:00Z",
        },
    }

    mock_load_sso_config.return_value = fake_fetcher_kwargs
    async with patched_session.create_client(
        "s3", region_name="us-east-1"
    ) as client_one:
        mock_load_sso_credentials.return_value = fake_response

        await _assert_feature_ids_in_ua(client_one, ['r', 's'])


@pytest.mark.parametrize(
    "config_content,env_vars,expected_source_features,expected_provider_feature",
    [
        # Test Case 1: Assume Role with source profile
        (
            '''[profile assume-role-test]
role_arn = arn:aws:iam::123456789012:role/test-role
source_profile = base

[profile base]
aws_access_key_id = FAKEACCESSKEY
aws_secret_access_key = FAKESECRET''',
            {},
            [
                'n',  # CREDENTIALS_PROFILE
                'o',  # CREDENTIALS_PROFILE_SOURCE_PROFILE
            ],
            'i',  # CREDENTIALS_STS_ASSUME_ROLE
        ),
        # Test Case 2: Assume Role with named provider
        (
            '''[profile assume-role-test]
role_arn = arn:aws:iam::123456789012:role/test-role
credential_source = Environment''',
            {
                'AWS_ACCESS_KEY_ID': 'FAKEACCESSKEY',
                'AWS_SECRET_ACCESS_KEY': 'FAKESECRET',
            },
            [
                'g',  # CREDENTIALS_ENV_VARS
                'p',  # CREDENTIALS_PROFILE_NAMED_PROVIDER
            ],
            'i',  # CREDENTIALS_STS_ASSUME_ROLE
        ),
    ],
)
async def test_user_agent_has_assume_role_feature_ids(
    config_content,
    env_vars,
    expected_source_features,
    expected_provider_feature,
    tmp_path,
):
    session = _create_assume_role_session(config_content, tmp_path)

    # Set env vars if needed
    with patch.dict(os.environ, env_vars, clear=True):
        with SessionHTTPStubber(session) as stubber:
            async with session.create_client(
                's3', region_name='us-east-1'
            ) as s3:
                _add_assume_role_http_response(
                    stubber, with_web_identity=False
                )
                stubber.add_response()
                stubber.add_response()
                await s3.list_buckets()
                await s3.list_buckets()

    ua_strings = get_captured_ua_strings(stubber)
    _assert_deferred_credential_feature_ids(
        ua_strings, expected_source_features, expected_provider_feature
    )


@pytest.mark.parametrize(
    "config_content,env_vars,expected_source_features,expected_provider_feature",
    [
        # Test Case 1: Assume Role with Web Identity through config profile
        (
            '''[profile assume-role-test]
role_arn = arn:aws:iam::123456789012:role/test-role
web_identity_token_file = {token_file}''',
            {},
            ['q'],  # CREDENTIALS_PROFILE_STS_WEB_ID_TOKEN
            'k',  # CREDENTIALS_STS_ASSUME_ROLE_WEB_ID
        ),
        # Test Case 2: Assume Role with Web Identity through env vars
        (
            '',
            {
                'AWS_ROLE_ARN': 'arn:aws:iam::123456789012:role/test-role',
                'AWS_WEB_IDENTITY_TOKEN_FILE': '{token_file}',
                'AWS_ROLE_SESSION_NAME': 'test-session',
            },
            ['h'],  # CREDENTIALS_ENV_VARS_STS_WEB_ID_TOKEN
            'k',  # CREDENTIALS_STS_ASSUME_ROLE_WEB_ID
        ),
    ],
)
async def test_user_agent_has_assume_role_with_web_identity_feature_ids(
    config_content,
    env_vars,
    expected_source_features,
    expected_provider_feature,
    tmp_path,
):
    token_file = tmp_path / 'token.jwt'
    token_file.write_text('fake-jwt-token')
    if 'AWS_WEB_IDENTITY_TOKEN_FILE' in env_vars:
        env_vars['AWS_WEB_IDENTITY_TOKEN_FILE'] = str(token_file)
    elif config_content and 'web_identity_token_file' in config_content:
        config_content = config_content.replace(
            '{token_file}', str(token_file)
        )

    session = _create_assume_role_session(config_content, tmp_path)

    # Set env vars if needed
    with patch.dict(os.environ, env_vars, clear=True):
        with SessionHTTPStubber(session) as stubber:
            async with session.create_client(
                's3', region_name='us-east-1'
            ) as s3:
                _add_assume_role_http_response(stubber, with_web_identity=True)
                stubber.add_response()
                stubber.add_response()
                await s3.list_buckets()
                await s3.list_buckets()

    ua_strings = get_captured_ua_strings(stubber)
    _assert_deferred_credential_feature_ids(
        ua_strings, expected_source_features, expected_provider_feature
    )


def _create_assume_role_session(config_content, tmp_path):
    if config_content:
        config_file = tmp_path / 'config'
        config_file.write_text(config_content)
        session = AioSession(profile='assume-role-test')
        session.set_config_variable('config_file', str(config_file))
    else:
        session = AioSession()
    return session


def _add_assume_role_http_response(stubber, with_web_identity):
    """Add HTTP response for AssumeRole or AssumeRoleWithWebIdentity call with proper credentials"""
    expiration = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )
    method_name = (
        'AssumeRoleWithWebIdentity' if with_web_identity else 'AssumeRole'
    )
    body = (
        f'<{method_name}Response>'
        f'  <{method_name}Result>'
        '    <AssumedRoleUser>'
        '      <Arn>arn:aws:sts::123456789012:user</Arn>'
        '      <AssumedRoleId>AKID:test-session-123</AssumedRoleId>'
        '    </AssumedRoleUser>'
        '    <Credentials>'
        f'      <AccessKeyId>FAKEASSUMEROLEKEY</AccessKeyId>'
        f'      <SecretAccessKey>FAKEASSUMEROLSECRET</SecretAccessKey>'
        '      <SessionToken>FAKETOKEN</SessionToken>'
        f'      <Expiration>{expiration}</Expiration>'
        '    </Credentials>'
        f'  </{method_name}Result>'
        f'</{method_name}Response>'
    )
    stubber.add_response(body=body.encode('utf-8'))


def _assert_deferred_credential_feature_ids(
    ua_strings,
    expected_source_features,
    expected_provider_feature,
):
    """Helper to assert feature IDs for deferred credential provider tests"""
    assert len(ua_strings) == 3

    # Request to fetch credentials should only register feature ids for the credential source
    credential_source_feature_list = parse_registered_feature_ids(
        ua_strings[0]
    )
    for feature in expected_source_features:
        assert feature in credential_source_feature_list
    assert expected_provider_feature not in credential_source_feature_list

    # Original operation request should register feature ids for both the credential source and the provider
    for i in [1, 2]:
        operation_feature_list = parse_registered_feature_ids(ua_strings[i])
        for feature in expected_source_features:
            assert feature in operation_feature_list
        assert expected_provider_feature in operation_feature_list
