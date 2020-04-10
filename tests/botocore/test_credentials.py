"""
These tests have been taken from
https://github.com/boto/botocore/blob/develop/tests/unit/test_credentials.py
and adapted to work with asyncio and pytest
"""
import asyncio
import datetime
import json
import subprocess

import mock
from typing import Optional

import pytest
import botocore.exceptions
from dateutil.tz import tzlocal

from aiobotocore.session import AioSession
from aiobotocore import credentials
from botocore.configprovider import ConfigValueStore
from botocore.utils import FileWebIdentityTokenLoader


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
        future_time = datetime.datetime.now(tzlocal()) + datetime.timedelta(hours=24)
        expiry_time = datetime.datetime.now(tzlocal()) - datetime.timedelta(minutes=30)
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
        mock_time_return_value=(datetime.datetime.now(tzlocal()) -
                                datetime.timedelta(minutes=60))
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
        mock_time_return_value=datetime.datetime.now(tzlocal()),
        refresher_return_value={}
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_none(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.datetime.now(tzlocal()),
        refresher_return_value=None
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_partial(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.datetime.now(tzlocal()),
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
        future_time = datetime.datetime.now(tzlocal()) + datetime.timedelta(hours=24)
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
                                  datetime.datetime.now(tzlocal()))
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
    timeobj = datetime.datetime.now(tzlocal())
    return timeobj + datetime.timedelta(hours=24)


def get_expected_creds_from_response(response):
    expiration = response['Credentials']['Expiration']
    if isinstance(expiration, datetime.datetime):
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
                'Expiration': datetime.datetime.now(tzlocal()),
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
    timeobj = datetime.datetime.now(tzlocal())
    timestamp = (timeobj + datetime.timedelta(hours=24)).isoformat()

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

    local_now = mock.Mock(return_value=datetime.datetime.now(tzlocal()))
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
    future = datetime.datetime.now(tzlocal()) + datetime.timedelta(hours=24)

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

    timeobj = datetime.datetime.now(tzlocal())
    timestamp = (timeobj + datetime.timedelta(hours=24)).isoformat()
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
    expiry_time = datetime.datetime.now(tzlocal()) - datetime.timedelta(hours=1)
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
