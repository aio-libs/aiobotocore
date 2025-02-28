#!/usr/bin/env
# Copyright (c) 2012-2013 Mitch Garnaat http://garnaat.org/
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
import logging
import os

import botocore.exceptions
import pytest
from _pytest.logging import LogCaptureFixture
from botocore import UNSIGNED
from botocore.configprovider import ConfigChainFactory
from botocore.model import ServiceModel

import aiobotocore.config
from aiobotocore import client
from aiobotocore.session import ClientCreatorContext
from tests.botocore_tests import create_session, mock, temporary_file


class BaseSessionTest:
    @pytest.fixture
    def environ(self):
        environ = {}
        environ['FOO_PROFILE'] = 'foo'
        environ['FOO_REGION'] = 'us-west-11'
        data_path = os.path.join(os.path.dirname(__file__), 'data')
        environ['FOO_DATA_PATH'] = data_path
        config_path = os.path.join(
            os.path.dirname(__file__), 'cfg', 'foo_config'
        )
        environ['FOO_CONFIG_FILE'] = config_path
        return environ

    @pytest.fixture
    def environ_patch(self, environ):
        with mock.patch('os.environ', environ) as environ_patch:
            yield environ_patch

    @pytest.fixture
    def session(self, environ, environ_patch):
        session = create_session()
        config_chain_builder = ConfigChainFactory(
            session=session,
            environ=environ,
        )
        config_store = session.get_component('config_store')
        config_updates = {
            'profile': config_chain_builder.create_config_chain(
                instance_name='profile',
                env_var_names='FOO_PROFILE',
            ),
            'region': config_chain_builder.create_config_chain(
                instance_name='region',
                env_var_names='FOO_REGION',
                config_property_names='foo_region',
            ),
            'data_path': config_chain_builder.create_config_chain(
                instance_name='data_path',
                env_var_names='FOO_DATA_PATH',
                config_property_names='data_path',
            ),
            'config_file': config_chain_builder.create_config_chain(
                instance_name='config_file',
                env_var_names='FOO_CONFIG_FILE',
            ),
            'credentials_file': config_chain_builder.create_config_chain(
                instance_name='credentials_file',
                default='/tmp/nowhere',
            ),
            'ca_bundle': config_chain_builder.create_config_chain(
                instance_name='ca_bundle',
                env_var_names='FOO_AWS_CA_BUNDLE',
                config_property_names='foo_ca_bundle',
            ),
            'api_versions': config_chain_builder.create_config_chain(
                instance_name='api_versions',
                config_property_names='foo_api_versions',
                default={},
            ),
        }
        for name, provider in config_updates.items():
            config_store.set_config_provider(name, provider)

        return session


class TestSessionPartitionFiles(BaseSessionTest):
    def test_lists_partitions_on_disk(self, session):
        mock_resolver = mock.Mock()
        mock_resolver.get_available_partitions.return_value = ['foo']
        session._register_internal_component(
            'endpoint_resolver', mock_resolver
        )
        assert ['foo'] == session.get_available_partitions()

    async def test_proxies_list_endpoints_to_resolver(self, session):
        resolver = mock.Mock()
        resolver.get_available_endpoints.return_value = ['a', 'b']
        session._register_internal_component('endpoint_resolver', resolver)
        await session.get_available_regions('foo', 'bar', True)

    async def test_provides_empty_list_for_unknown_service_regions(
        self, session
    ):
        regions = await session.get_available_regions('__foo__')
        assert [] == regions

    def test_provides_correct_partition_for_region(self, session):
        partition = session.get_partition_for_region('us-west-2')
        assert partition == 'aws'

    def test_provides_correct_partition_for_region_regex(self, session):
        partition = session.get_partition_for_region('af-south-99')
        assert partition == 'aws'

    def test_provides_correct_partition_for_region_non_default(self, session):
        partition = session.get_partition_for_region('cn-north-1')
        assert partition == 'aws-cn'

    def test_raises_exception_for_invalid_region(self, session):
        with pytest.raises(botocore.exceptions.UnknownRegionError):
            session.get_partition_for_region('no-good-1')


class TestGetServiceModel(BaseSessionTest):
    async def test_get_service_model(self, session):
        loader = mock.Mock()
        loader.load_service_model.return_value = {
            'metadata': {'serviceId': 'foo'}
        }
        session.register_component('data_loader', loader)
        model = await session.get_service_model('made_up')
        assert isinstance(model, ServiceModel)
        assert model.service_name == 'made_up'


class TestCreateClient(BaseSessionTest):
    async def test_can_create_client(self, session):
        sts_client_context = session.create_client('sts', 'us-west-2')
        assert isinstance(sts_client_context, ClientCreatorContext)
        async with sts_client_context as sts_client:
            assert isinstance(sts_client, client.AioBaseClient)

    async def test_credential_provider_not_called_when_creds_provided(
        self, session
    ):
        cred_provider = mock.Mock()
        session.register_component('credential_provider', cred_provider)
        async with session.create_client(
            'sts',
            'us-west-2',
            aws_access_key_id='foo',
            aws_secret_access_key='bar',
            aws_session_token='baz',
        ):
            assert not cred_provider.load_credentials.called

    async def test_cred_provider_called_when_partial_creds_provided(
        self, session
    ):
        with pytest.raises(botocore.exceptions.PartialCredentialsError):
            async with session.create_client(
                'sts',
                'us-west-2',
                aws_access_key_id='foo',
                aws_secret_access_key=None,
            ):
                pass  # pragma: no cover
        with pytest.raises(botocore.exceptions.PartialCredentialsError):
            async with session.create_client(
                'sts',
                'us-west-2',
                aws_access_key_id=None,
                aws_secret_access_key='foo',
            ):
                pass  # pragma: no cover

    async def test_cred_provider_not_called_on_unsigned_client(self, session):
        cred_provider = mock.Mock()
        session.register_component('credential_provider', cred_provider)
        config = aiobotocore.config.AioConfig(signature_version=UNSIGNED)
        async with session.create_client('sts', 'us-west-2', config=config):
            assert not cred_provider.load_credentials.called

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_config_passed_to_client_creator(
        self, client_creator, session
    ):
        # Make sure there is no default set
        assert session.get_default_client_config() is None

        # The config passed to the client should be the one that is used
        # in creating the client.
        config = aiobotocore.config.AioConfig(region_name='us-west-2')
        async with session.create_client('sts', config=config):
            client_creator.return_value.create_client.assert_called_with(
                service_name=mock.ANY,
                region_name=mock.ANY,
                is_secure=mock.ANY,
                endpoint_url=mock.ANY,
                verify=mock.ANY,
                credentials=mock.ANY,
                scoped_config=mock.ANY,
                client_config=config,
                api_version=mock.ANY,
                auth_token=mock.ANY,
            )

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_default_client_config(
        self, client_creator, session
    ):
        config = aiobotocore.config.AioConfig()
        session.set_default_client_config(config)
        async with session.create_client('sts'):
            client_creator.return_value.create_client.assert_called_with(
                service_name=mock.ANY,
                region_name=mock.ANY,
                is_secure=mock.ANY,
                endpoint_url=mock.ANY,
                verify=mock.ANY,
                credentials=mock.ANY,
                scoped_config=mock.ANY,
                client_config=config,
                api_version=mock.ANY,
                auth_token=mock.ANY,
            )

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_merging_client_configs(
        self, client_creator, session
    ):
        config = aiobotocore.config.AioConfig(region_name='us-west-2')
        other_config = aiobotocore.config.AioConfig(region_name='us-east-1')
        session.set_default_client_config(config)
        async with session.create_client('sts', config=other_config):
            # Grab the client config used in creating the client
            used_client_config = (
                client_creator.return_value.create_client.call_args[1][
                    'client_config'
                ]
            )
            # Check that the client configs were merged
            assert used_client_config.region_name == 'us-east-1'
            # Make sure that the client config used is not the default client
            # config or the one passed in. It should be a new config.
            assert used_client_config is not config
            assert used_client_config is not other_config

    async def test_create_client_with_region(self, session):
        async with session.create_client('ec2', 'us-west-2') as ec2_client:
            assert ec2_client.meta.region_name == 'us-west-2'

    async def test_create_client_with_region_and_client_config(self, session):
        config = aiobotocore.config.AioConfig()
        # Use a client config with no region configured.
        async with session.create_client(
            'ec2', region_name='us-west-2', config=config
        ) as ec2_client:
            assert ec2_client.meta.region_name == 'us-west-2'

            # If the region name is changed, it should not change the
            # region of the client
            config.region_name = 'us-east-1'
            assert ec2_client.meta.region_name == 'us-west-2'

        # Now make a new client with the updated client config.
        async with session.create_client('ec2', config=config) as ec2_client:
            assert ec2_client.meta.region_name == 'us-east-1'

    async def test_create_client_no_region_and_no_client_config(self, session):
        async with session.create_client('ec2') as ec2_client:
            assert ec2_client.meta.region_name == 'us-west-11'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_ca_bundle_from_config(
        self, client_creator, environ, session
    ):
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write('foo_ca_bundle=config-certs.pem\n')
            f.flush()

            async with session.create_client('ec2', 'us-west-2'):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                assert call_kwargs['verify'] == 'config-certs.pem'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_ca_bundle_from_env_var(
        self, client_creator, environ, session
    ):
        environ['FOO_AWS_CA_BUNDLE'] = 'env-certs.pem'
        async with session.create_client('ec2', 'us-west-2'):
            call_kwargs = client_creator.return_value.create_client.call_args[
                1
            ]
            assert call_kwargs['verify'] == 'env-certs.pem'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_verify_param(
        self, client_creator, session
    ):
        async with session.create_client(
            'ec2', 'us-west-2', verify='verify-certs.pem'
        ):
            call_kwargs = client_creator.return_value.create_client.call_args[
                1
            ]
            assert call_kwargs['verify'] == 'verify-certs.pem'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_verify_param_overrides_all(
        self, client_creator, environ, session
    ):
        with temporary_file('w') as f:
            # Set the ca cert using the config file
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write('foo_ca_bundle=config-certs.pem\n')
            f.flush()

            # Set the ca cert with an environment variable
            environ['FOO_AWS_CA_BUNDLE'] = 'env-certs.pem'

            # Set the ca cert using the verify parameter
            async with session.create_client(
                'ec2', 'us-west-2', verify='verify-certs.pem'
            ):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                # The verify parameter should override all the other
                # configurations
                assert call_kwargs['verify'] == 'verify-certs.pem'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_use_no_api_version_by_default(
        self, client_creator, session
    ):
        async with session.create_client('myservice', 'us-west-2'):
            call_kwargs = client_creator.return_value.create_client.call_args[
                1
            ]
            assert call_kwargs['api_version'] is None

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_uses_api_version_from_config(
        self, client_creator, environ, session
    ):
        config_api_version = '2012-01-01'
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write(
                'foo_api_versions =\n'
                f'    myservice = {config_api_version}\n'
            )
            f.flush()

            async with session.create_client('myservice', 'us-west-2'):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                assert call_kwargs['api_version'] == config_api_version

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_can_specify_multiple_versions_from_config(
        self, client_creator, environ, session
    ):
        config_api_version = '2012-01-01'
        second_config_api_version = '2013-01-01'
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write(
                f'foo_api_versions =\n'
                f'    myservice = {config_api_version}\n'
                f'    myservice2 = {second_config_api_version}\n'
            )
            f.flush()

            async with session.create_client('myservice', 'us-west-2'):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                assert call_kwargs['api_version'] == config_api_version

            async with session.create_client('myservice2', 'us-west-2'):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                assert call_kwargs['api_version'] == second_config_api_version

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_param_api_version_overrides_config_value(
        self, client_creator, environ, session
    ):
        config_api_version = '2012-01-01'
        override_api_version = '2014-01-01'
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write(
                'foo_api_versions =\n'
                f'    myservice = {config_api_version}\n'
            )
            f.flush()

            async with session.create_client(
                'myservice', 'us-west-2', api_version=override_api_version
            ):
                call_kwargs = (
                    client_creator.return_value.create_client.call_args[1]
                )
                assert call_kwargs['api_version'] == override_api_version

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_credentials(
        self, client_creator, session
    ):
        async with session.create_client(
            'sts',
            'us-west-2',
            aws_access_key_id='foo',
            aws_secret_access_key='bar',
            aws_session_token='baz',
            aws_account_id='bin',
        ):
            credentials = (
                client_creator.return_value.create_client.call_args.kwargs[
                    'credentials'
                ]
            )
            assert credentials.access_key == 'foo'
            assert credentials.secret_key == 'bar'
            assert credentials.token == 'baz'
            assert credentials.account_id == 'bin'

    @mock.patch('aiobotocore.session.AioClientCreator', autospec=True)
    async def test_create_client_with_ignored_credentials(
        self, client_creator, session, caplog: LogCaptureFixture
    ):
        caplog.set_level(logging.DEBUG, 'botocore.session')
        async with session.create_client(
            'sts',
            'us-west-2',
            aws_account_id='foo',
        ):
            credentials = (
                client_creator.return_value.create_client.call_args.kwargs[
                    'credentials'
                ]
            )
            assert (
                'Ignoring the following credential-related values'
                in caplog.text
            )
            assert 'aws_account_id' in caplog.text
            assert credentials.account_id is None


class TestClientMonitoring(BaseSessionTest):
    async def assert_created_client_is_monitored(self, session):
        with mock.patch(
            'botocore.monitoring.Monitor', spec=True
        ) as mock_monitor:
            async with session.create_client('ec2', 'us-west-2') as client:
                mock_monitor.return_value.register.assert_called_with(
                    client.meta.events
                )

    async def assert_monitoring_host_and_port(self, session, host, port):
        with mock.patch(
            'botocore.monitoring.SocketPublisher', spec=True
        ) as mock_publisher:
            async with session.create_client('ec2', 'us-west-2'):
                assert mock_publisher.call_count == 1
                _, args, kwargs = mock_publisher.mock_calls[0]
                assert kwargs.get('host') == host
                assert kwargs.get('port') == port

    async def assert_created_client_is_not_monitored(self, session):
        with mock.patch(
            'botocore.session.monitoring.Monitor', spec=True
        ) as mock_monitor:
            async with session.create_client('ec2', 'us-west-2'):
                mock_monitor.return_value.register.assert_not_called()

    async def test_with_csm_enabled_from_config(self, environ, session):
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write('csm_enabled=true\n')
            f.flush()
            await self.assert_created_client_is_monitored(session)

    async def test_with_csm_enabled_from_env(self, environ, session):
        environ['AWS_CSM_ENABLED'] = 'true'
        await self.assert_created_client_is_monitored(session)

    async def test_with_csm_host(self, environ, session):
        custom_host = '10.13.37.1'
        environ['AWS_CSM_ENABLED'] = 'true'
        environ['AWS_CSM_HOST'] = custom_host
        await self.assert_monitoring_host_and_port(session, custom_host, 31000)

    async def test_with_csm_port(self, environ, session):
        custom_port = '1234'
        environ['AWS_CSM_ENABLED'] = 'true'
        environ['AWS_CSM_PORT'] = custom_port
        await self.assert_monitoring_host_and_port(
            session,
            '127.0.0.1',
            int(custom_port),
        )

    async def test_with_csm_disabled_from_config(self, environ, session):
        with temporary_file('w') as f:
            del environ['FOO_PROFILE']
            environ['FOO_CONFIG_FILE'] = f.name
            f.write('[default]\n')
            f.write('csm_enabled=false\n')
            f.flush()
            await self.assert_created_client_is_not_monitored(session)

    async def test_with_csm_disabled_from_env(self, environ, session):
        environ['AWS_CSM_ENABLED'] = 'false'
        await self.assert_created_client_is_not_monitored(session)

    async def test_csm_not_configured(self, session):
        await self.assert_created_client_is_not_monitored(session)
