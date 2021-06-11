"""
These tests have been taken from
https://github.com/boto/botocore/blob/develop/tests/unit/test_credentials.py
and adapted to work with asyncio and pytest
"""
import asyncio
import binascii
import os
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
import subprocess
from unittest import TestCase
from functools import partial

from unittest import mock
from typing import Optional

import pytest
import botocore.exceptions
from botocore.stub import Stubber
from dateutil.tz import tzlocal, tzutc
from botocore.utils import datetime2timestamp

from aiobotocore.session import AioSession
from aiobotocore import credentials

from botocore.credentials import Credentials, JSONFileCache, ReadOnlyCredentials
from botocore.configprovider import ConfigValueStore
from botocore.utils import FileWebIdentityTokenLoader, SSOTokenLoader
from aiobotocore.credentials import AioSSOCredentialFetcher, AioSSOProvider, \
    AioInstanceMetadataProvider, AioEnvProvider, AioContainerProvider, \
    AioAssumeRoleProvider, AioProfileProviderBuilder, AioCanonicalNameCredentialSourcer
from tests.botocore.helpers import StubbedSession


def random_chars(num_chars):
    return binascii.hexlify(os.urandom(int(num_chars / 2))).decode('ascii')


# From class TestCredentials(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.parametrize("access,secret", [
    ('foo\xe2\x80\x99', 'bar\xe2\x80\x99'), (u'foo', u'bar')])
def test_credentials_normalization(access, secret):
    c = credentials.AioCredentials(access, secret)
    assert isinstance(c.access_key, type(u'u'))
    assert isinstance(c.secret_key, type(u'u'))


# From class TestRefreshableCredentials(TestCredentials):
@pytest.fixture
def refreshable_creds():
    def _f(mock_time_return_value=None, refresher_return_value='METADATA'):
        refresher = mock.AsyncMock()
        future_time = datetime.now(tzlocal()) + timedelta(hours=24)
        expiry_time = datetime.now(tzlocal()) - timedelta(minutes=30)
        metadata = {
            'access_key': 'NEW-ACCESS',
            'secret_key': 'NEW-SECRET',
            'token': 'NEW-TOKEN',
            'expiry_time': future_time.isoformat(),
            'role_name': 'rolename',
        }
        refresher.return_value = metadata if refresher_return_value == 'METADATA' \
            else refresher_return_value
        mock_time = mock.Mock()
        mock_time.return_value = mock_time_return_value
        creds = credentials.AioRefreshableCredentials(
            'ORIGINAL-ACCESS', 'ORIGINAL-SECRET', 'ORIGINAL-TOKEN',
            expiry_time, refresher, 'iam-role', time_fetcher=mock_time
        )
        return creds
    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_get_credentials_set(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=(datetime.now(tzlocal()) -
                                timedelta(minutes=60))
    )

    assert not creds.refresh_needed()

    credentials_set = await creds.get_frozen_credentials()
    assert isinstance(credentials_set, credentials.ReadOnlyCredentials)
    assert credentials_set.access_key == 'ORIGINAL-ACCESS'
    assert credentials_set.secret_key == 'ORIGINAL-SECRET'
    assert credentials_set.token == 'ORIGINAL-TOKEN'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_empty_dict(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.now(tzlocal()),
        refresher_return_value={}
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_none(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.now(tzlocal()),
        refresher_return_value=None
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_partial(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.now(tzlocal()),
        refresher_return_value={'access_key': 'akid'}
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


# From class TestDeferredRefreshableCredentials(unittest.TestCase):
@pytest.fixture
def deferrable_creds():
    def _f(mock_time_return_value=None, refresher_return_value='METADATA'):
        refresher = mock.AsyncMock()
        future_time = datetime.now(tzlocal()) + timedelta(hours=24)
        metadata = {
            'access_key': 'NEW-ACCESS',
            'secret_key': 'NEW-SECRET',
            'token': 'NEW-TOKEN',
            'expiry_time': future_time.isoformat(),
            'role_name': 'rolename',
        }
        refresher.return_value = metadata if refresher_return_value == 'METADATA' \
            else refresher_return_value
        mock_time = mock.Mock()
        mock_time.return_value = (mock_time_return_value or
                                  datetime.now(tzlocal()))
        creds = credentials.AioDeferredRefreshableCredentials(
            refresher, 'iam-role', time_fetcher=mock_time
        )
        return creds
    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_deferrablecredentials_get_credentials_set(deferrable_creds):
    creds = deferrable_creds()

    creds._refresh_using.assert_not_called()

    await creds.get_frozen_credentials()
    assert creds._refresh_using.call_count == 1


@pytest.mark.moto
@pytest.mark.asyncio
async def test_deferrablecredentials_refresh_only_called_once(deferrable_creds):
    creds = deferrable_creds()

    creds._refresh_using.assert_not_called()

    for _ in range(5):
        await creds.get_frozen_credentials()

    assert creds._refresh_using.call_count == 1


# From class TestAssumeRoleCredentialFetcher(BaseEnvVar):
def assume_role_client_creator(with_response):
    class _Client(object):
        def __init__(self, resp):
            self._resp = resp

            self._called = []
            self._call_count = 0

        async def assume_role(self, *args, **kwargs):
            self._call_count += 1
            self._called.append((args, kwargs))

            if isinstance(self._resp, list):
                return self._resp.pop(0)
            return self._resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return mock.Mock(return_value=_Client(with_response))


def some_future_time():
    timeobj = datetime.now(tzlocal())
    return timeobj + timedelta(hours=24)


def get_expected_creds_from_response(response):
    expiration = response['Credentials']['Expiration']
    if isinstance(expiration, datetime):
        expiration = expiration.isoformat()
    return {
        'access_key': response['Credentials']['AccessKeyId'],
        'secret_key': response['Credentials']['SecretAccessKey'],
        'token': response['Credentials']['SessionToken'],
        'expiry_time': expiration
    }


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolefetcher_no_cache():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    refresher = credentials.AioAssumeRoleCredentialFetcher(
        assume_role_client_creator(response),
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole'
    )

    expected_response = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected_response


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolefetcher_cache_key_with_role_session_name():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    cache = {}
    client_creator = assume_role_client_creator(response)
    role_session_name = 'my_session_name'

    refresher = credentials.AioAssumeRoleCredentialFetcher(
        client_creator,
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole',
        cache=cache,
        extra_args={'RoleSessionName': role_session_name}
    )
    await refresher.fetch_credentials()

    # This is the sha256 hex digest of the expected assume role args.
    cache_key = (
        '2964201f5648c8be5b9460a9cf842d73a266daf2'
    )
    assert cache_key in cache
    assert cache[cache_key] == response


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolefetcher_cache_in_cache_but_expired():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat(),
        },
    }
    client_creator = assume_role_client_creator(response)
    cache = {
        'development--myrole': {
            'Credentials': {
                'AccessKeyId': 'foo-cached',
                'SecretAccessKey': 'bar-cached',
                'SessionToken': 'baz-cached',
                'Expiration': datetime.now(tzlocal()),
            }
        }
    }

    refresher = credentials.AioAssumeRoleCredentialFetcher(
        client_creator,
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole',
        cache=cache
    )
    expected = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolefetcher_mfa():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat(),
        },
    }
    client_creator = assume_role_client_creator(response)
    prompter = mock.Mock(return_value='token-code')
    mfa_serial = 'mfa'

    refresher = credentials.AioAssumeRoleCredentialFetcher(
        client_creator,
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole',
        extra_args={'SerialNumber': mfa_serial}, mfa_prompter=prompter
    )
    await refresher.fetch_credentials()

    # Slighly different to the botocore mock
    client = client_creator.return_value
    assert client._call_count == 1
    call_kwargs = client._called[0][1]
    assert call_kwargs['SerialNumber'] == 'mfa'
    assert call_kwargs['RoleArn'] == 'myrole'
    assert call_kwargs['TokenCode'] == 'token-code'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_recursive_assume_role(assume_role_setup):
    self = assume_role_setup

    config = (
        '[profile A]\n'
        'role_arn = arn:aws:iam::123456789:role/RoleA\n'
        'source_profile = B\n\n'
        '[profile B]\n'
        'role_arn = arn:aws:iam::123456789:role/RoleB\n'
        'source_profile = C\n\n'
        '[profile C]\n'
        'aws_access_key_id = abc123\n'
        'aws_secret_access_key = def456\n'
    )
    self.write_config(config)

    profile_b_creds = self.create_random_credentials()
    profile_b_response = self.create_assume_role_response(profile_b_creds)
    profile_a_creds = self.create_random_credentials()
    profile_a_response = self.create_assume_role_response(profile_a_creds)

    async with self.create_session(profile='A') as (session, stubber):
        stubber.add_response('assume_role', profile_b_response)
        stubber.add_response('assume_role', profile_a_response)

        actual_creds = await session.get_credentials()
        await self.assert_creds_equal(actual_creds, profile_a_creds)
        stubber.assert_no_pending_responses()


# From class TestAssumeRoleWithWebIdentityCredentialFetcher(BaseEnvVar):
def assume_role_web_identity_client_creator(with_response):
    class _Client(object):
        def __init__(self, resp):
            self._resp = resp

            self._called = []
            self._call_count = 0

        async def assume_role_with_web_identity(self, *args, **kwargs):
            self._call_count += 1
            self._called.append((args, kwargs))

            if isinstance(self._resp, list):
                return self._resp.pop(0)
            return self._resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return mock.Mock(return_value=_Client(with_response))


@pytest.mark.moto
@pytest.mark.asyncio
async def test_webidentfetcher_no_cache():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    refresher = credentials.AioAssumeRoleWithWebIdentityCredentialFetcher(
        assume_role_web_identity_client_creator(response),
        lambda: 'totally.a.token',
        'myrole'
    )

    expected_response = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected_response


# From class TestInstanceMetadataProvider(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_instancemetadata_load():
    timeobj = datetime.now(tzlocal())
    timestamp = (timeobj + timedelta(hours=24)).isoformat()

    fetcher = mock.AsyncMock()
    fetcher.retrieve_iam_role_credentials.return_value = {
        'access_key': 'a',
        'secret_key': 'b',
        'token': 'c',
        'expiry_time': timestamp,
        'role_name': 'myrole',
    }

    provider = credentials.AioInstanceMetadataProvider(
        iam_role_fetcher=fetcher
    )
    creds = await provider.load()
    assert creds is not None
    assert creds.method == 'iam-role'

    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.token == 'c'


# From class CredentialResolverTest(BaseEnvVar):
@pytest.fixture
def credential_provider():
    def _f(method, canonical_name, creds='None'):
        # 'None' so that we can differentiate from None
        provider = mock.AsyncMock()
        provider.METHOD = method
        provider.CANONICAL_NAME = canonical_name
        if creds != 'None':
            provider.load.return_value = creds
        return provider
    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_credresolver_load_credentials_single_provider(credential_provider):
    provider1 = credential_provider('provider1', 'CustomProvider1',
                                    credentials.AioCredentials('a', 'b', 'c'))
    resolver = credentials.AioCredentialResolver(providers=[provider1])

    creds = await resolver.load_credentials()
    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.token == 'c'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_credresolver_no_providers(credential_provider):
    provider1 = credential_provider('provider1', 'CustomProvider1',
                                    None)
    resolver = credentials.AioCredentialResolver(providers=[provider1])

    creds = await resolver.load_credentials()
    assert creds is None


# From class TestCanonicalNameSourceProvider(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_canonicalsourceprovider_source_creds(credential_provider):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('CustomProvider1')
    assert result is creds


@pytest.mark.moto
@pytest.mark.asyncio
async def test_canonicalsourceprovider_source_creds_case_insensitive(
        credential_provider):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('cUsToMpRoViDeR1')
    assert result is creds


# From class TestAssumeRoleCredentialProvider(unittest.TestCase):
@pytest.fixture
def assumerolecredprovider_config_loader():
    fake_config = {
        'profiles': {
            'development': {
                'role_arn': 'myrole',
                'source_profile': 'longterm',
            },
            'longterm': {
                'aws_access_key_id': 'akid',
                'aws_secret_access_key': 'skid',
            },
            'non-static': {
                'role_arn': 'myrole',
                'credential_source': 'Environment'
            },
            'chained': {
                'role_arn': 'chained-role',
                'source_profile': 'development'
            }
        }
    }

    def _f(config=None):
        return lambda: (config or fake_config)

    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolecredprovider_assume_role_no_cache(
        credential_provider,
        assumerolecredprovider_config_loader):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('cUsToMpRoViDeR1')
    assert result is creds

    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    client_creator = assume_role_client_creator(response)
    provider = credentials.AioAssumeRoleProvider(
        assumerolecredprovider_config_loader(),
        client_creator, cache={}, profile_name='development')

    creds = await provider.load()

    # So calling .access_key would cause deferred credentials to be loaded,
    # according to the source, you're supposed to call get_frozen_credentials
    # so will do that.
    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.token == 'baz'


# MFA
@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolecredprovider_mfa(
        credential_provider,
        assumerolecredprovider_config_loader):

    fake_config = {
        'profiles': {
            'development': {
                'role_arn': 'myrole',
                'source_profile': 'longterm',
                'mfa_serial': 'mfa'
            },
            'longterm': {
                'aws_access_key_id': 'akid',
                'aws_secret_access_key': 'skid',
            },
            'non-static': {
                'role_arn': 'myrole',
                'credential_source': 'Environment'
            },
            'chained': {
                'role_arn': 'chained-role',
                'source_profile': 'development'
            }
        }
    }

    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    client_creator = assume_role_client_creator(response)
    prompter = mock.Mock(return_value='token-code')
    provider = credentials.AioAssumeRoleProvider(
        assumerolecredprovider_config_loader(fake_config),
        client_creator, cache={}, profile_name='development', prompter=prompter)

    creds = await provider.load()
    # So calling .access_key would cause deferred credentials to be loaded,
    # according to the source, you're supposed to call get_frozen_credentials
    # so will do that.
    await creds.get_frozen_credentials()

    client = client_creator.return_value
    assert client._call_count == 1
    call_kwargs = client._called[0][1]
    assert call_kwargs['SerialNumber'] == 'mfa'
    assert call_kwargs['RoleArn'] == 'myrole'
    assert call_kwargs['TokenCode'] == 'token-code'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolecredprovider_mfa_cannot_refresh_credentials(
        credential_provider,
        assumerolecredprovider_config_loader):

    fake_config = {
        'profiles': {
            'development': {
                'role_arn': 'myrole',
                'source_profile': 'longterm',
                'mfa_serial': 'mfa'
            },
            'longterm': {
                'aws_access_key_id': 'akid',
                'aws_secret_access_key': 'skid',
            },
            'non-static': {
                'role_arn': 'myrole',
                'credential_source': 'Environment'
            },
            'chained': {
                'role_arn': 'chained-role',
                'source_profile': 'development'
            }
        }
    }

    expiration_time = some_future_time()
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': expiration_time.isoformat()
        },
    }
    client_creator = assume_role_client_creator(response)
    prompter = mock.Mock(return_value='token-code')
    provider = credentials.AioAssumeRoleProvider(
        assumerolecredprovider_config_loader(fake_config),
        client_creator, cache={}, profile_name='development', prompter=prompter)

    local_now = mock.Mock(return_value=datetime.now(tzlocal()))
    with mock.patch('aiobotocore.credentials._local_now', local_now):
        creds = await provider.load()
        await creds.get_frozen_credentials()

        local_now.return_value = expiration_time
        with pytest.raises(credentials.RefreshWithMFAUnsupportedError):
            await creds.get_frozen_credentials()


# From class TestAssumeRoleWithWebIdentityCredentialProvider
@pytest.mark.moto
@pytest.mark.asyncio
async def test_assumerolewebidentprovider_no_cache():
    future = datetime.now(tzlocal()) + timedelta(hours=24)

    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': future.isoformat()
        },
    }

    # client
    client_creator = assume_role_web_identity_client_creator(response)

    mock_loader = mock.Mock(spec=FileWebIdentityTokenLoader)
    mock_loader.return_value = 'totally.a.token'
    mock_loader_cls = mock.Mock(return_value=mock_loader)

    config = {
        'profiles': {
            'some-profile': {
                'role_arn': 'arn:aws:iam::123:role/role-name',
                'web_identity_token_file': '/some/path/token.jwt'
            }
        }
    }

    provider = credentials.AioAssumeRoleWithWebIdentityProvider(
        load_config=lambda: config,
        client_creator=client_creator,
        cache={},
        profile_name='some-profile',
        token_loader_cls=mock_loader_cls
    )

    creds = await provider.load()
    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.token == 'baz'

    mock_loader_cls.assert_called_with('/some/path/token.jwt')


# From class TestContainerProvider(BaseEnvVar):
def full_url(url):
    return 'http://%s%s' % (credentials.AioContainerMetadataFetcher.IP_ADDRESS, url)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_containerprovider_assume_role_no_cache():
    environ = {
        'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI': '/latest/credentials?id=foo'
    }
    fetcher = mock.AsyncMock()
    fetcher.full_url = full_url

    timeobj = datetime.now(tzlocal())
    timestamp = (timeobj + timedelta(hours=24)).isoformat()
    fetcher.retrieve_full_uri.return_value = {
        "AccessKeyId": "access_key",
        "SecretAccessKey": "secret_key",
        "Token": "token",
        "Expiration": timestamp,
    }
    provider = credentials.AioContainerProvider(environ, fetcher)
    # Will return refreshable credentials
    creds = await provider.load()

    url = full_url('/latest/credentials?id=foo')
    fetcher.retrieve_full_uri.assert_called_with(url, headers=None)

    assert creds.method == 'container-role'

    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'access_key'
    assert creds.secret_key == 'secret_key'
    assert creds.token == 'token'


# From class TestEnvVar(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_envvarprovider_env_var_present():
    environ = {
        'AWS_ACCESS_KEY_ID': 'foo',
        'AWS_SECRET_ACCESS_KEY': 'bar',
    }
    provider = credentials.AioEnvProvider(environ)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)

    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.method == 'env'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_envvarprovider_env_var_absent():
    environ = {}
    provider = credentials.AioEnvProvider(environ)
    creds = await provider.load()
    assert creds is None


@pytest.mark.moto
@pytest.mark.asyncio
async def test_envvarprovider_env_var_expiry():
    expiry_time = datetime.now(tzlocal()) - timedelta(hours=1)
    environ = {
        'AWS_ACCESS_KEY_ID': 'foo',
        'AWS_SECRET_ACCESS_KEY': 'bar',
        'AWS_CREDENTIAL_EXPIRATION': expiry_time.isoformat()
    }
    provider = credentials.AioEnvProvider(environ)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioRefreshableCredentials)

    del environ['AWS_CREDENTIAL_EXPIRATION']

    with pytest.raises(botocore.exceptions.PartialCredentialsError):
        await creds.get_frozen_credentials()


# From class TestConfigFileProvider(BaseEnvVar):
@pytest.fixture
def profile_config():
    parser = mock.Mock()
    profile_config = {
        'aws_access_key_id': 'a',
        'aws_secret_access_key': 'b',
        'aws_session_token': 'c',
        # Non creds related configs can be in a session's # config.
        'region': 'us-west-2',
        'output': 'json',
    }
    parsed = {'profiles': {'default': profile_config}}
    parser.return_value = parsed
    return parser


@pytest.mark.moto
@pytest.mark.asyncio
async def test_configprovider_file_exists(profile_config):
    provider = credentials.AioConfigProvider('cli.cfg', 'default', profile_config)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)

    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.method == 'config-file'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_configprovider_file_missing_profile(profile_config):
    provider = credentials.AioConfigProvider('cli.cfg', 'NOT-default', profile_config)
    creds = await provider.load()
    assert creds is None


# From class TestSharedCredentialsProvider(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_sharedcredentials_file_exists():
    parser = mock.Mock()
    parser.return_value = {
        'default': {
            'aws_access_key_id': 'foo',
            'aws_secret_access_key': 'bar',
        }
    }

    provider = credentials.AioSharedCredentialProvider(
        creds_filename='~/.aws/creds', profile_name='default',
        ini_parser=parser)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)

    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.method == 'shared-credentials-file'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_sharedcredentials_file_missing():
    parser = mock.Mock()
    parser.side_effect = botocore.exceptions.ConfigNotFound(path='foo')

    provider = credentials.AioSharedCredentialProvider(
        creds_filename='~/.aws/creds', profile_name='dev',
        ini_parser=parser)
    creds = await provider.load()
    assert creds is None


# From class TestBotoProvider(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_botoprovider_file_exists():
    parser = mock.Mock()
    parser.return_value = {
        'Credentials': {
            'aws_access_key_id': 'a',
            'aws_secret_access_key': 'b',
        }
    }

    provider = credentials.AioBotoProvider(environ={}, ini_parser=parser)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)

    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.method == 'boto-config'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_botoprovider_file_missing():
    parser = mock.Mock()
    parser.side_effect = botocore.exceptions.ConfigNotFound(path='foo')

    provider = credentials.AioBotoProvider(environ={}, ini_parser=parser)
    creds = await provider.load()
    assert creds is None


# From class TestOriginalEC2Provider(BaseEnvVar):
@pytest.mark.moto
@pytest.mark.asyncio
async def test_originalec2provider_file_exists():
    envrion = {'AWS_CREDENTIAL_FILE': 'foo.cfg'}
    parser = mock.Mock()
    parser.return_value = {
        'AWSAccessKeyId': 'a',
        'AWSSecretKey': 'b',
    }

    provider = credentials.AioOriginalEC2Provider(environ=envrion, parser=parser)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)

    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.method == 'ec2-credentials-file'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_originalec2provider_file_missing():
    provider = credentials.AioOriginalEC2Provider(environ={})
    creds = await provider.load()
    assert creds is None


# From class TestProcessProvider
@pytest.fixture()
def process_provider():
    def _f(profile_name='default', loaded_config=None, invoked_process=None):
        load_config = mock.Mock(return_value=loaded_config)
        popen_mock = mock.Mock(return_value=invoked_process or mock.Mock(),
                               spec=asyncio.create_subprocess_exec)
        return popen_mock, credentials.AioProcessProvider(profile_name,
                                                          load_config,
                                                          popen=popen_mock)
    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_retrieve_refereshable_creds(process_provider):
    config = {'profiles': {'default': {'credential_process': 'my-process /somefile'}}}
    invoked_process = mock.AsyncMock()
    stdout = json.dumps({
        'Version': 1,
        'AccessKeyId': 'foo',
        'SecretAccessKey': 'bar',
        'SessionToken': 'baz',
        'Expiration': '2999-01-01T00:00:00Z',
    })
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 0

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioRefreshableCredentials)
    assert creds is not None
    assert creds.method == 'custom-process'

    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.token == 'baz'
    popen_mock.assert_called_with('my-process', '/somefile',
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_retrieve_creds(process_provider):
    config = {'profiles': {'default': {'credential_process': 'my-process'}}}
    invoked_process = mock.AsyncMock()
    stdout = json.dumps({
        'Version': 1,
        'AccessKeyId': 'foo',
        'SecretAccessKey': 'bar',
        'SessionToken': 'baz'
    })
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 0

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    creds = await provider.load()
    assert isinstance(creds, credentials.AioCredentials)
    assert creds is not None
    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.token == 'baz'
    assert creds.method == 'custom-process'


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_bad_version(process_provider):
    config = {'profiles': {'default': {'credential_process': 'my-process'}}}
    invoked_process = mock.AsyncMock()
    stdout = json.dumps({
        'Version': 2,
        'AccessKeyId': 'foo',
        'SecretAccessKey': 'bar',
        'SessionToken': 'baz',
        'Expiration': '2999-01-01T00:00:00Z',
    })
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 0

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await provider.load()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_missing_field(process_provider):
    config = {'profiles': {'default': {'credential_process': 'my-process'}}}
    invoked_process = mock.AsyncMock()
    stdout = json.dumps({
        'Version': 1,
        'SecretAccessKey': 'bar',
        'SessionToken': 'baz',
        'Expiration': '2999-01-01T00:00:00Z',
    })
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 0

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await provider.load()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_bad_exitcode(process_provider):
    config = {'profiles': {'default': {'credential_process': 'my-process'}}}
    invoked_process = mock.AsyncMock()
    stdout = 'lah'
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 1

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await provider.load()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_processprovider_bad_config(process_provider):
    config = {'profiles': {'default': {'credential_process': None}}}
    invoked_process = mock.AsyncMock()
    stdout = json.dumps({
        'Version': 2,
        'AccessKeyId': 'foo',
        'SecretAccessKey': 'bar',
        'SessionToken': 'baz',
        'Expiration': '2999-01-01T00:00:00Z',
    })
    invoked_process.communicate.return_value = \
        (stdout.encode('utf-8'), ''.encode('utf-8'))
    invoked_process.returncode = 0

    popen_mock, provider = process_provider(
        loaded_config=config, invoked_process=invoked_process)
    creds = await provider.load()
    assert creds is None


# From class TestCreateCredentialResolver
@pytest.fixture
def mock_session():
    def _f(config_loader: Optional[ConfigValueStore] = None) -> AioSession:
        if not config_loader:
            config_loader = ConfigValueStore()

        fake_instance_variables = {
            'credentials_file': 'a',
            'legacy_config_file': 'b',
            'config_file': 'c',
            'metadata_service_timeout': 1,
            'metadata_service_num_attempts': 1,
            'imds_use_ipv6': 'false',
        }

        def fake_get_component(self, key):
            if key == 'config_provider':
                return config_loader
            return None

        def fake_set_config_variable(self, logical_name, value):
            fake_instance_variables[logical_name] = value

        session = mock.Mock(spec=AioSession)
        session.get_component = fake_get_component
        session.full_config = {}

        for name, value in fake_instance_variables.items():
            config_loader.set_config_variable(name, value)

        session.get_config_variable = config_loader.get_config_variable
        session.set_config_variable = fake_set_config_variable

        return session
    return _f


@pytest.mark.moto
@pytest.mark.asyncio
async def test_createcredentialresolver(mock_session):
    session = mock_session()

    resolver = credentials.create_credential_resolver(session)
    assert isinstance(resolver, credentials.AioCredentialResolver)


# Disabled on travis as we cant easily disable the tests properly and
#  travis has an IAM role which can't be applied to the mock session
# @pytest.mark.moto
@pytest.mark.asyncio
async def test_get_credentials(mock_session):
    session = mock_session()

    creds = await credentials.get_credentials(session)

    assert creds is None


@pytest.mark.moto
@pytest.mark.asyncio
async def test_from_aiocredentials_is_none():
    creds = credentials.AioCredentials.from_credentials(None)
    assert creds is None
    creds = credentials.AioRefreshableCredentials.from_refreshable_credentials(None)
    assert creds is None


@pytest.mark.moto
@pytest.mark.asyncio
async def test_session_credentials():
    with mock.patch('aiobotocore.credentials.AioCredential'
                    'Resolver.load_credentials') as mock_obj:
        mock_obj.return_value = 'somecreds'

        session = AioSession()
        creds = await session.get_credentials()
        assert creds == 'somecreds'


class Self:
    pass


class _AsyncCtx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# From class TestSSOCredentialFetcher:
@pytest.fixture
async def ssl_credential_fetcher_setup():
    async with AioSession().create_client('sso', region_name='us-east-1') as sso:
        self = Self()
        self.sso = sso
        self.stubber = Stubber(self.sso)
        self.mock_session = mock.Mock(spec=AioSession)
        self.mock_session.create_client.return_value = _AsyncCtx(sso)

        self.cache = {}
        self.sso_region = 'us-east-1'
        self.start_url = 'https://d-92671207e4.awsapps.com/start'
        self.role_name = 'test-role'
        self.account_id = '1234567890'
        self.access_token = 'some.sso.token'
        # This is just an arbitrary point in time we can pin to
        self.now = datetime(2008, 9, 23, 12, 26, 40, tzinfo=tzutc())
        # The SSO endpoint uses ms whereas the OIDC endpoint uses seconds
        self.now_timestamp = 1222172800000

        self.loader = mock.Mock(spec=SSOTokenLoader)
        self.loader.return_value = self.access_token
        self.fetcher = AioSSOCredentialFetcher(
            self.start_url, self.sso_region, self.role_name, self.account_id,
            self.mock_session.create_client, token_loader=self.loader,
            cache=self.cache,
        )

        tc = TestCase()
        self.assertEqual = tc.assertEqual
        self.assertRaises = tc.assertRaises
        yield self


@pytest.fixture
def base_env_var_setup():
    self = Self()
    self.environ = {}
    with mock.patch('os.environ', self.environ):
        yield self


def _some_future_time():
    timeobj = datetime.now(tzlocal())
    return timeobj + timedelta(hours=24)


def _create_assume_role_response(credentials, expiration=None):
    if expiration is None:
        expiration = _some_future_time()

    response = {
        'Credentials': {
            'AccessKeyId': credentials.access_key,
            'SecretAccessKey': credentials.secret_key,
            'SessionToken': credentials.token,
            'Expiration': expiration
        },
        'AssumedRoleUser': {
            'AssumedRoleId': 'myroleid',
            'Arn': 'arn:aws:iam::1234567890:user/myuser'
        }
    }

    return response


def _create_random_credentials():
    return Credentials(
        'fake-%s' % random_chars(15),
        'fake-%s' % random_chars(35),
        'fake-%s' % random_chars(45)
    )


async def _assert_creds_equal(c1, c2):
    c1_frozen = c1
    if not isinstance(c1_frozen, ReadOnlyCredentials):
        c1_frozen = await c1.get_frozen_credentials()
    c2_frozen = c2
    if not isinstance(c2_frozen, ReadOnlyCredentials):
        c2_frozen = c2.get_frozen_credentials()
    assert c1_frozen == c2_frozen


def _write_config(self, config):
    with open(self.config_file, 'w') as f:
        f.write(config)


@pytest.fixture
def base_assume_role_test_setup(base_env_var_setup):
    self = base_env_var_setup
    with tempfile.TemporaryDirectory() as td_name:
        self.tempdir = td_name
        self.config_file = os.path.join(self.tempdir, 'config')
        self.environ['AWS_CONFIG_FILE'] = self.config_file
        self.environ['AWS_SHARED_CREDENTIALS_FILE'] = str(uuid.uuid4())

        self.some_future_time = _some_future_time
        self.create_assume_role_response = _create_assume_role_response
        self.create_random_credentials = _create_random_credentials
        self.assert_creds_equal = _assert_creds_equal
        self.write_config = partial(_write_config, self)

        yield self


def _mock_provider(provider_cls):
    mock_instance = mock.Mock(spec=provider_cls)
    mock_instance.load.return_value = None
    mock_instance.METHOD = provider_cls.METHOD
    mock_instance.CANONICAL_NAME = provider_cls.CANONICAL_NAME
    return mock_instance


@asynccontextmanager
async def _create_session(self, profile=None):
    session = StubbedSession(profile=profile)

    # We have to set bogus credentials here or otherwise we'll trigger
    # an early credential chain resolution.
    async with session.create_client(
        'sts',
        aws_access_key_id='spam',
        aws_secret_access_key='eggs',
    ) as sts:
        self.mock_client_creator.return_value = sts
        assume_role_provider = AioAssumeRoleProvider(
            load_config=lambda: session.full_config,
            client_creator=self.mock_client_creator,
            cache={},
            profile_name=profile,
            credential_sourcer=AioCanonicalNameCredentialSourcer([
                self.env_provider, self.container_provider,
                self.metadata_provider
            ]),
            profile_provider_builder=AioProfileProviderBuilder(
                session,
                sso_token_cache=JSONFileCache(self.tempdir),
            ),
        )
        async with session.stub('sts') as stubber:
            stubber.activate()

            component_name = 'credential_provider'
            resolver = session.get_component(component_name)
            available_methods = [p.METHOD for p in resolver.providers]
            replacements = {
                'env': self.env_provider,
                'iam-role': self.metadata_provider,
                'container-role': self.container_provider,
                'assume-role': assume_role_provider
            }
            for name, provider in replacements.items():
                try:
                    index = available_methods.index(name)
                except ValueError:
                    # The provider isn't in the session
                    continue

                resolver.providers[index] = provider

            session.register_component(
                'credential_provider', resolver
            )
            yield session, stubber


@pytest.fixture
def assume_role_setup(base_assume_role_test_setup):
    self = base_assume_role_test_setup

    self.environ['AWS_ACCESS_KEY_ID'] = 'access_key'
    self.environ['AWS_SECRET_ACCESS_KEY'] = 'secret_key'

    self.mock_provider = _mock_provider
    self.create_session = partial(_create_session, self)

    self.metadata_provider = self.mock_provider(AioInstanceMetadataProvider)
    self.env_provider = self.mock_provider(AioEnvProvider)
    self.container_provider = self.mock_provider(AioContainerProvider)
    self.mock_client_creator = mock.Mock(spec=AioSession.create_client)
    self.actual_client_region = None

    current_dir = os.path.dirname(os.path.abspath(__file__))
    credential_process = os.path.join(
        current_dir, 'utils', 'credentialprocess.py'
    )
    self.credential_process = '%s %s' % (
        sys.executable, credential_process
    )

    yield self


@pytest.mark.moto
@pytest.mark.asyncio
async def test_sso_credential_fetcher_can_fetch_credentials(
        ssl_credential_fetcher_setup):
    self = ssl_credential_fetcher_setup
    expected_params = {
        'roleName': self.role_name,
        'accountId': self.account_id,
        'accessToken': self.access_token,
    }
    expected_response = {
        'roleCredentials': {
            'accessKeyId': 'foo',
            'secretAccessKey': 'bar',
            'sessionToken': 'baz',
            'expiration': self.now_timestamp + 1000000,
        }
    }
    self.stubber.add_response(
        'get_role_credentials',
        expected_response,
        expected_params=expected_params,
    )
    with self.stubber:
        credentials = await self.fetcher.fetch_credentials()
    self.assertEqual(credentials['access_key'], 'foo')
    self.assertEqual(credentials['secret_key'], 'bar')
    self.assertEqual(credentials['token'], 'baz')
    self.assertEqual(credentials['expiry_time'], '2008-09-23T12:43:20Z')
    cache_key = '048db75bbe50955c16af7aba6ff9c41a3131bb7e'
    expected_cached_credentials = {
        'ProviderType': 'sso',
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': '2008-09-23T12:43:20Z',
        }
    }
    self.assertEqual(self.cache[cache_key], expected_cached_credentials)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_sso_cred_fetcher_raises_helpful_message_on_unauthorized_exception(
        ssl_credential_fetcher_setup):
    self = ssl_credential_fetcher_setup
    expected_params = {
        'roleName': self.role_name,
        'accountId': self.account_id,
        'accessToken': self.access_token,
    }
    self.stubber.add_client_error(
        'get_role_credentials',
        service_error_code='UnauthorizedException',
        expected_params=expected_params,
    )
    with self.assertRaises(botocore.exceptions.UnauthorizedSSOTokenError):
        with self.stubber:
            await self.fetcher.fetch_credentials()


# from TestSSOProvider
@pytest.fixture
async def sso_provider_setup():
    self = Self()
    async with AioSession().create_client('sso', region_name='us-east-1') as sso:
        self.sso = sso
        self.stubber = Stubber(self.sso)
        self.mock_session = mock.Mock(spec=AioSession)
        self.mock_session.create_client.return_value = _AsyncCtx(sso)

        self.sso_region = 'us-east-1'
        self.start_url = 'https://d-92671207e4.awsapps.com/start'
        self.role_name = 'test-role'
        self.account_id = '1234567890'
        self.access_token = 'some.sso.token'

        self.profile_name = 'sso-profile'
        self.config = {
            'sso_region': self.sso_region,
            'sso_start_url': self.start_url,
            'sso_role_name': self.role_name,
            'sso_account_id': self.account_id,
        }
        self.expires_at = datetime.now(tzlocal()) + timedelta(hours=24)
        self.cached_creds_key = '048db75bbe50955c16af7aba6ff9c41a3131bb7e'
        self.cached_token_key = '13f9d35043871d073ab260e020f0ffde092cb14b'
        self.cache = {
            self.cached_token_key: {
                'accessToken': self.access_token,
                'expiresAt': self.expires_at.strftime('%Y-%m-%dT%H:%M:%S%Z'),
            }
        }
        self._mock_load_config = partial(_mock_load_config, self)
        self._add_get_role_credentials_response = partial(
            _add_get_role_credentials_response, self)
        self.provider = AioSSOProvider(
            load_config=self._mock_load_config,
            client_creator=self.mock_session.create_client,
            profile_name=self.profile_name,
            cache=self.cache,
            token_cache=self.cache,
        )

        self.expected_get_role_credentials_params = {
            'roleName': self.role_name,
            'accountId': self.account_id,
            'accessToken': self.access_token,
        }
        expiration = datetime2timestamp(self.expires_at)
        self.expected_get_role_credentials_response = {
            'roleCredentials': {
                'accessKeyId': 'foo',
                'secretAccessKey': 'bar',
                'sessionToken': 'baz',
                'expiration': int(expiration * 1000),
            }
        }

        tc = TestCase()
        self.assertEqual = tc.assertEqual
        self.assertRaises = tc.assertRaises

        yield self


def _mock_load_config(self):
    return {
        'profiles': {
            self.profile_name: self.config,
        }
    }


def _add_get_role_credentials_response(self):
    self.stubber.add_response(
        'get_role_credentials',
        self.expected_get_role_credentials_response,
        self.expected_get_role_credentials_params,
    )


def test_load_sso_credentials_without_cache(self):
    self._add_get_role_credentials_response()
    with self.stubber:
        credentials = self.provider.load()
        self.assertEqual(credentials.access_key, 'foo')
        self.assertEqual(credentials.secret_key, 'bar')
        self.assertEqual(credentials.token, 'baz')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_load_sso_credentials_with_cache(sso_provider_setup):
    self = sso_provider_setup

    cached_creds = {
        'Credentials': {
            'AccessKeyId': 'cached-akid',
            'SecretAccessKey': 'cached-sak',
            'SessionToken': 'cached-st',
            'Expiration': self.expires_at.strftime('%Y-%m-%dT%H:%M:%S%Z'),
        }
    }
    self.cache[self.cached_creds_key] = cached_creds
    credentials = await self.provider.load()
    credentials = await credentials.get_frozen_credentials()
    self.assertEqual(credentials.access_key, 'cached-akid')
    self.assertEqual(credentials.secret_key, 'cached-sak')
    self.assertEqual(credentials.token, 'cached-st')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_load_sso_credentials_with_cache_expired(sso_provider_setup):
    self = sso_provider_setup
    cached_creds = {
        'Credentials': {
            'AccessKeyId': 'expired-akid',
            'SecretAccessKey': 'expired-sak',
            'SessionToken': 'expired-st',
            'Expiration': '2002-10-22T20:52:11UTC',
        }
    }
    self.cache[self.cached_creds_key] = cached_creds

    self._add_get_role_credentials_response()
    with self.stubber:
        credentials = await self.provider.load()
        credentials = await credentials.get_frozen_credentials()

        self.assertEqual(credentials.access_key, 'foo')
        self.assertEqual(credentials.secret_key, 'bar')
        self.assertEqual(credentials.token, 'baz')


@pytest.mark.moto
@pytest.mark.asyncio
async def test_required_config_not_set(sso_provider_setup):
    self = sso_provider_setup
    del self.config['sso_start_url']
    # If any required configuration is missing we should get an error
    with self.assertRaises(botocore.exceptions.InvalidConfigError):
        await self.provider.load()
