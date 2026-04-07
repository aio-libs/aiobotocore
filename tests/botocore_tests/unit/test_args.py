#!/usr/bin/env
# Copyright 2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
from botocore.client import ClientEndpointBridge
from botocore.configprovider import ConfigValueStore
from botocore.useragent import UserAgentString

from aiobotocore import args
from aiobotocore.config import AioConfig
from aiobotocore.credentials import AioCredentials
from aiobotocore.hooks import AioHierarchicalEmitter
from tests.botocore_tests import mock, unittest


class TestEndpointResolverBuiltins(unittest.TestCase):
    def setUp(self):
        event_emitter = mock.Mock(AioHierarchicalEmitter)
        self.config_store = ConfigValueStore()
        user_agent_creator = UserAgentString(
            platform_name=None,
            platform_version=None,
            platform_machine=None,
            python_version=None,
            python_implementation=None,
            execution_env=None,
            crt_version=None,
        )
        self.args_create = args.AioClientArgsCreator(
            event_emitter=event_emitter,
            user_agent=None,
            response_parser_factory=None,
            loader=None,
            exceptions_factory=None,
            config_store=self.config_store,
            user_agent_creator=user_agent_creator,
        )
        self.bridge = ClientEndpointBridge(
            endpoint_resolver=mock.Mock(),
            scoped_config=None,
            client_config=AioConfig(),
            default_endpoint=None,
            service_signing_name=None,
            config_store=self.config_store,
        )
        # assume a legacy endpoint resolver that uses the builtin
        # endpoints.json file
        self.bridge.endpoint_resolver.uses_builtin_data = True

    def call_compute_endpoint_resolver_builtin_defaults(self, **overrides):
        defaults = {
            'region_name': 'ca-central-1',
            'service_name': 'fooservice',
            's3_disable_express_session_auth': False,
            's3_config': {},
            'endpoint_bridge': self.bridge,
            'client_endpoint_url': None,
            'legacy_endpoint_url': 'https://my.legacy.endpoint.com',
            'credentials': None,
            'account_id_endpoint_mode': 'preferred',
        }
        kwargs = {**defaults, **overrides}
        return self.args_create.compute_endpoint_resolver_builtin_defaults(
            **kwargs
        )

    def test_builtins_defaults(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults()
        self.assertEqual(bins['AWS::Region'], 'ca-central-1')
        self.assertEqual(bins['AWS::UseFIPS'], False)
        self.assertEqual(bins['AWS::UseDualStack'], False)
        self.assertEqual(bins['AWS::STS::UseGlobalEndpoint'], False)
        self.assertEqual(bins['AWS::S3::UseGlobalEndpoint'], False)
        self.assertEqual(bins['AWS::S3::Accelerate'], False)
        self.assertEqual(bins['AWS::S3::ForcePathStyle'], False)
        self.assertEqual(bins['AWS::S3::UseArnRegion'], True)
        self.assertEqual(bins['AWS::S3Control::UseArnRegion'], False)
        self.assertEqual(
            bins['AWS::S3::DisableMultiRegionAccessPoints'], False
        )
        self.assertEqual(bins['AWS::S3::DisableS3ExpressSessionAuth'], False)
        self.assertEqual(bins['SDK::Endpoint'], None)
        self.assertEqual(bins['AWS::Auth::AccountId'], None)
        self.assertEqual(bins['AWS::Auth::AccountIdEndpointMode'], 'preferred')

    def test_aws_region(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            region_name='my-region-1',
        )
        self.assertEqual(bins['AWS::Region'], 'my-region-1')

    def test_aws_use_fips_when_config_is_set_true(self):
        self.config_store.set_config_variable('use_fips_endpoint', True)
        bins = self.call_compute_endpoint_resolver_builtin_defaults()
        self.assertEqual(bins['AWS::UseFIPS'], True)

    def test_aws_use_fips_when_config_is_set_false(self):
        self.config_store.set_config_variable('use_fips_endpoint', False)
        bins = self.call_compute_endpoint_resolver_builtin_defaults()
        self.assertEqual(bins['AWS::UseFIPS'], False)

    def test_aws_use_dualstack_when_config_is_set_true(self):
        self.bridge.client_config = AioConfig(
            s3={'use_dualstack_endpoint': True}
        )
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            service_name='s3-control'
        )
        self.assertEqual(bins['AWS::UseDualStack'], True)

    def test_aws_use_dualstack_when_config_is_set_false(self):
        self.bridge.client_config = AioConfig(
            s3={'use_dualstack_endpoint': False}
        )
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            service_name='s3-control'
        )
        self.assertEqual(bins['AWS::UseDualStack'], False)

    def test_aws_use_dualstack_when_non_dualstack_service(self):
        self.bridge.client_config = AioConfig(
            s3={'use_dualstack_endpoint': True}
        )
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            service_name='other-service'
        )
        self.assertEqual(bins['AWS::UseDualStack'], False)

    def test_aws_sts_global_endpoint_with_default_and_legacy_region(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            region_name='us-west-2',
        )
        self.assertEqual(bins['AWS::STS::UseGlobalEndpoint'], False)

    def test_aws_sts_global_endpoint_with_default_and_nonlegacy_region(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            region_name='eu-south-1',
        )
        self.assertEqual(bins['AWS::STS::UseGlobalEndpoint'], False)

    def test_aws_sts_global_endpoint_with_nondefault_config(self):
        self.config_store.set_config_variable(
            'sts_regional_endpoints', 'regional'
        )
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            region_name='us-west-2',
        )
        self.assertEqual(bins['AWS::STS::UseGlobalEndpoint'], False)

    def test_s3_global_endpoint(self):
        # The only reason for this builtin to not have the default value
        # (False) is that the ``_should_force_s3_global`` method
        # returns True.
        self.args_create._should_force_s3_global = mock.Mock(return_value=True)
        bins = self.call_compute_endpoint_resolver_builtin_defaults()
        self.assertTrue(bins['AWS::S3::UseGlobalEndpoint'])
        self.args_create._should_force_s3_global.assert_called_once()

    def test_s3_accelerate_with_config_set_true(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'use_accelerate_endpoint': True},
        )
        self.assertEqual(bins['AWS::S3::Accelerate'], True)

    def test_s3_accelerate_with_config_set_false(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'use_accelerate_endpoint': False},
        )
        self.assertEqual(bins['AWS::S3::Accelerate'], False)

    def test_force_path_style_with_config_set_to_path(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'addressing_style': 'path'},
        )
        self.assertEqual(bins['AWS::S3::ForcePathStyle'], True)

    def test_force_path_style_with_config_set_to_auto(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'addressing_style': 'auto'},
        )
        self.assertEqual(bins['AWS::S3::ForcePathStyle'], False)

    def test_force_path_style_with_config_set_to_virtual(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'addressing_style': 'virtual'},
        )
        self.assertEqual(bins['AWS::S3::ForcePathStyle'], False)

    def test_use_arn_region_with_config_set_false(self):
        # These two builtins both take their value from the ``use_arn_region``
        # in the S3 configuration, but have different default values.
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'use_arn_region': False},
        )
        self.assertEqual(bins['AWS::S3::UseArnRegion'], False)
        self.assertEqual(bins['AWS::S3Control::UseArnRegion'], False)

    def test_use_arn_region_with_config_set_true(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'use_arn_region': True},
        )
        self.assertEqual(bins['AWS::S3::UseArnRegion'], True)
        self.assertEqual(bins['AWS::S3Control::UseArnRegion'], True)

    def test_disable_mrap_with_config_set_true(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'s3_disable_multiregion_access_points': True},
        )
        self.assertEqual(bins['AWS::S3::DisableMultiRegionAccessPoints'], True)

    def test_disable_mrap_with_config_set_false(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_config={'s3_disable_multiregion_access_points': False},
        )
        self.assertEqual(
            bins['AWS::S3::DisableMultiRegionAccessPoints'], False
        )

    def test_sdk_endpoint_both_inputs_set(self):
        # assume a legacy endpoint resolver that uses a customized
        # endpoints.json file
        self.bridge.endpoint_resolver.uses_builtin_data = False
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            client_endpoint_url='https://my.client.endpoint.com',
            legacy_endpoint_url='https://my.legacy.endpoint.com',
        )
        self.assertEqual(
            bins['SDK::Endpoint'], 'https://my.client.endpoint.com'
        )

    def test_sdk_endpoint_legacy_set_with_builtin_data(self):
        # assume a legacy endpoint resolver that uses a customized
        # endpoints.json file
        self.bridge.endpoint_resolver.uses_builtin_data = False
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            client_endpoint_url=None,
            legacy_endpoint_url='https://my.legacy.endpoint.com',
        )
        self.assertEqual(
            bins['SDK::Endpoint'], 'https://my.legacy.endpoint.com'
        )

    def test_sdk_endpoint_legacy_set_without_builtin_data(self):
        # assume a legacy endpoint resolver that uses the builtin
        # endpoints.json file
        self.bridge.endpoint_resolver.uses_builtin_data = True
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            client_endpoint_url=None,
            legacy_endpoint_url='https://my.legacy.endpoint.com',
        )
        self.assertEqual(bins['SDK::Endpoint'], None)

    def test_account_id_set_with_credentials(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            credentials=AioCredentials(
                access_key='foo', secret_key='bar', account_id='baz'
            )
        )
        self.assertEqual(bins['AWS::Auth::AccountId'](), 'baz')

    def test_account_id_endpoint_mode_set_to_disabled(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            account_id_endpoint_mode='disabled'
        )
        self.assertEqual(bins['AWS::Auth::AccountIdEndpointMode'], 'disabled')

    def test_disable_s3_express_session_auth_default(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults()
        self.assertEqual(bins['AWS::S3::DisableS3ExpressSessionAuth'], False)

    def test_disable_s3_express_session_auth_set_to_false(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_disable_express_session_auth=False,
        )
        self.assertEqual(bins['AWS::S3::DisableS3ExpressSessionAuth'], False)

    def test_disable_s3_express_session_auth_set_to_true(self):
        bins = self.call_compute_endpoint_resolver_builtin_defaults(
            s3_disable_express_session_auth=True,
        )
        self.assertEqual(bins['AWS::S3::DisableS3ExpressSessionAuth'], True)
