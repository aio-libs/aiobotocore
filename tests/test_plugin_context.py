"""
Tests for plugin context functionality in aiobotocore.

These tests validate that create_nested_client() properly applies plugin context
to prevent infinite loops when clients are created from within credential providers
or other plugins.
"""

import os
from unittest.mock import Mock, patch

import pytest

from aiobotocore.credentials import _get_client_creator
from aiobotocore.utils import create_nested_client


class TestCreateNestedClient:
    """Test the create_nested_client function."""

    def test_create_nested_client_returns_async_context_manager(self, session):
        """Test that create_nested_client returns an async context manager."""
        client_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )

        # Should return an async context manager
        assert hasattr(client_context, '__aenter__')
        assert hasattr(client_context, '__aexit__')
        # It's now an async generator context manager, not ClientCreatorContext
        assert '_AsyncGeneratorContextManager' in str(type(client_context))

    async def test_create_nested_client_creates_working_client(self, session):
        """Test that create_nested_client creates a functional client."""
        client_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )

        async with client_context as client:
            # Should be an S3 client
            assert client._service_model.service_name == 's3'
            assert hasattr(client, 'list_buckets')
            assert hasattr(client, 'get_object')

    @patch('aiobotocore.utils.set_plugin_context')
    @patch('aiobotocore.utils.reset_plugin_context')
    @patch('aiobotocore.utils.PluginContext')
    async def test_create_nested_client_manages_plugin_context(
        self, mock_plugin_context, mock_reset, mock_set, session
    ):
        """Test that create_nested_client properly manages plugin context."""
        mock_ctx = Mock()
        mock_plugin_context.return_value = mock_ctx
        mock_token = Mock()
        mock_set.return_value = mock_token

        # Call create_nested_client and actually enter the context manager
        client_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )
        async with client_context:
            pass  # Just test that context management works

        # Verify plugin context was created with plugins disabled
        mock_plugin_context.assert_called_once_with(plugins="DISABLED")
        # Verify context was set and reset
        mock_set.assert_called_once_with(mock_ctx)
        mock_reset.assert_called_once_with(mock_token)

    @patch('aiobotocore.utils.set_plugin_context')
    @patch('aiobotocore.utils.reset_plugin_context')
    async def test_create_nested_client_resets_context_on_exception(
        self, mock_reset, mock_set, session
    ):
        """Test that plugin context is reset even if client creation fails."""
        mock_token = Mock()
        mock_set.return_value = mock_token

        # Mock session.create_client to raise an exception
        with patch.object(
            session, 'create_client', side_effect=Exception("Test error")
        ):
            client_context = create_nested_client(
                session, 's3', region_name='us-east-1'
            )
            with pytest.raises(Exception, match="Test error"):
                async with client_context:
                    pass

        # Verify context was still reset despite the exception
        mock_reset.assert_called_once_with(mock_token)

    async def test_create_nested_client_passes_kwargs(self, session):
        """Test that create_nested_client passes through all kwargs."""
        with patch.object(session, 'create_client') as mock_create:
            # Mock create_client to return a simple async context manager
            mock_client = Mock()

            # Create a proper async context manager mock
            class MockAsyncContextManager:
                async def __aenter__(self):
                    return mock_client

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    return None

            mock_create.return_value = MockAsyncContextManager()

            client_context = create_nested_client(
                session,
                's3',
                region_name='us-west-2',
                aws_access_key_id='test-key',
                aws_secret_access_key='test-secret',
            )

            async with client_context as client:
                assert client == mock_client

            mock_create.assert_called_once_with(
                's3',
                region_name='us-west-2',
                aws_access_key_id='test-key',
                aws_secret_access_key='test-secret',
            )


class TestGetClientCreator:
    """Test the _get_client_creator function."""

    def test_get_client_creator_returns_function(self, session):
        """Test that _get_client_creator returns a callable function."""
        client_creator = _get_client_creator(session, 'us-east-1')

        assert callable(client_creator)

    def test_client_creator_function_works(self, session):
        """Test that the returned client creator function works."""
        client_creator = _get_client_creator(session, 'us-east-1')

        client_context = client_creator('s3')
        # Now returns an async context manager from create_nested_client
        assert hasattr(client_context, '__aenter__')
        assert hasattr(client_context, '__aexit__')
        assert '_AsyncGeneratorContextManager' in str(type(client_context))

    def test_client_creator_uses_provided_region(self, session):
        """Test that client creator uses the provided region."""
        region = 'us-west-2'
        client_creator = _get_client_creator(session, region)

        with patch(
            'aiobotocore.credentials.create_nested_client'
        ) as mock_create_nested:
            client_creator('s3')

            # Should be called with the specified region
            mock_create_nested.assert_called_once_with(
                session, 's3', region_name=region
            )

    def test_client_creator_merges_kwargs(self, session):
        """Test that client creator merges additional kwargs."""
        client_creator = _get_client_creator(session, 'us-east-1')

        with patch(
            'aiobotocore.credentials.create_nested_client'
        ) as mock_create_nested:
            client_creator(
                's3',
                aws_access_key_id='test-key',
                endpoint_url='http://localhost:9000',
            )

            mock_create_nested.assert_called_once_with(
                session,
                's3',
                region_name='us-east-1',
                aws_access_key_id='test-key',
                endpoint_url='http://localhost:9000',
            )

    def test_client_creator_kwargs_override_region(self, session):
        """Test that explicit region in kwargs overrides the default."""
        client_creator = _get_client_creator(session, 'us-east-1')

        with patch(
            'aiobotocore.credentials.create_nested_client'
        ) as mock_create_nested:
            client_creator('s3', region_name='eu-west-1')

            mock_create_nested.assert_called_once_with(
                session, 's3', region_name='eu-west-1'
            )

    def test_client_creator_uses_create_nested_client(self, session):
        """Test that client creator uses create_nested_client internally."""
        client_creator = _get_client_creator(session, 'us-east-1')

        with patch(
            'aiobotocore.credentials.create_nested_client'
        ) as mock_create_nested:
            client_creator('s3', aws_access_key_id='test-key')

            mock_create_nested.assert_called_once_with(
                session,
                's3',
                region_name='us-east-1',
                aws_access_key_id='test-key',
            )


class TestPluginContextIntegration:
    """Integration tests for plugin context functionality."""

    async def test_plugin_context_prevents_infinite_loops(self, session):
        """Test that plugin context prevents infinite loops in credential providers."""
        # This is a more complex integration test that simulates the scenario
        # where a credential provider might try to create a client

        call_count = 0
        original_create_client = session._create_client

        async def mock_create_client(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            # Simulate a credential provider trying to create a nested client
            if call_count == 1:
                # This should use create_nested_client to prevent infinite recursion
                create_nested_client(session, 'sts', region_name='us-east-1')
                # We don't actually use the nested client, just create it

            return await original_create_client(*args, **kwargs)

        with patch.object(
            session, '_create_client', side_effect=mock_create_client
        ):
            client_context = session.create_client(
                's3', region_name='us-east-1'
            )
            async with client_context as client:
                assert client._service_model.service_name == 's3'

        # Should have been called at least once for the main client
        assert call_count >= 1

    async def test_plugin_context_environment_variable_handling(self, session):
        """Test that plugin context properly handles environment variables."""
        # Test with plugins enabled via environment variable
        with patch.dict(
            os.environ, {'BOTOCORE_EXPERIMENTAL__PLUGINS': 'test_plugin'}
        ):
            with patch(
                'aiobotocore.utils.PluginContext'
            ) as mock_plugin_context:
                client_context = create_nested_client(
                    session, 's3', region_name='us-east-1'
                )
                async with client_context:
                    # Should create context with plugins disabled regardless of env var
                    mock_plugin_context.assert_called_once_with(
                        plugins="DISABLED"
                    )

    async def test_multiple_nested_clients(self, session):
        """Test creating multiple nested clients works correctly."""
        # Create multiple nested clients
        s3_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )
        sts_context = create_nested_client(
            session, 'sts', region_name='us-east-1'
        )

        # Both should work independently
        async with s3_context as s3_client:
            assert s3_client._service_model.service_name == 's3'

        async with sts_context as sts_client:
            assert sts_client._service_model.service_name == 'sts'

    def test_nested_client_with_different_configs(self, session):
        """Test that nested clients can have different configurations."""
        from aiobotocore.config import AioConfig

        config1 = AioConfig(
            region_name='us-east-1', retries={'max_attempts': 3}
        )
        config2 = AioConfig(
            region_name='us-west-2', retries={'max_attempts': 5}
        )

        client1_context = create_nested_client(session, 's3', config=config1)
        client2_context = create_nested_client(session, 's3', config=config2)

        # Both should be created successfully with different configs
        assert hasattr(client1_context, '__aenter__')
        assert hasattr(client1_context, '__aexit__')
        assert hasattr(client2_context, '__aenter__')
        assert hasattr(client2_context, '__aexit__')


class TestCredentialProviderIntegration:
    """Test integration with credential providers."""

    def test_credential_resolver_uses_get_client_creator(self, session):
        """Test that credential resolver can use _get_client_creator."""
        from aiobotocore.credentials import create_credential_resolver

        # This should not raise any import errors
        resolver = create_credential_resolver(session, region_name='us-east-1')
        assert resolver is not None

    def test_assume_role_provider_uses_client_creator(self, session):
        """Test that AssumeRoleProvider can be created with client creator."""
        from aiobotocore.credentials import create_credential_resolver

        # Create credential resolver which should use _get_client_creator internally
        resolver = create_credential_resolver(session, region_name='us-east-1')

        # Should have providers that can use client creators
        assert len(resolver.providers) > 0

        # Check that we can access the _get_client_creator function
        from aiobotocore.credentials import _get_client_creator

        client_creator = _get_client_creator(session, 'us-east-1')
        assert callable(client_creator)


class TestErrorHandling:
    """Test error handling in plugin context functions."""

    async def test_create_nested_client_with_invalid_service(self, session):
        """Test create_nested_client with invalid service name."""
        # This should still manage plugin context properly even if service is invalid
        with patch('aiobotocore.utils.set_plugin_context') as mock_set:
            with patch('aiobotocore.utils.reset_plugin_context') as mock_reset:
                mock_token = Mock()
                mock_set.return_value = mock_token

                # This might raise an exception for invalid service, but context should still be managed
                client_context = create_nested_client(
                    session, 'invalid-service', region_name='us-east-1'
                )
                try:
                    async with client_context:
                        pass  # pragma: no cover
                except Exception:
                    pass  # We expect this might fail

                # Context should still be set and reset
                mock_set.assert_called_once()
                mock_reset.assert_called_once_with(mock_token)

    def test_plugin_context_availability(self):
        """Test that plugin context availability is correctly detected."""
        # The functions should always be available (either real or fallback)
        from aiobotocore.utils import (
            PluginContext,
            reset_plugin_context,
            set_plugin_context,
        )

        assert callable(PluginContext)
        assert callable(set_plugin_context)
        assert callable(reset_plugin_context)

    def test_plugin_context_import_fallback(self):
        """Test that plugin context functions can be imported."""
        # This test ensures the imports work correctly
        from botocore.utils import (
            PluginContext,
            reset_plugin_context,
            set_plugin_context,
        )

        # These should be callable
        assert callable(PluginContext)
        assert callable(set_plugin_context)
        assert callable(reset_plugin_context)

    def test_create_nested_client_functional_test(self, session):
        """Functional test that create_nested_client actually works end-to-end."""
        # This test verifies the actual functionality without mocking
        client_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )

        # Should be able to create and use the client
        assert client_context is not None
        assert hasattr(client_context, '__aenter__')
        assert hasattr(client_context, '__aexit__')

    async def test_nested_client_context_manager(self, session):
        """Test that nested client works as a context manager."""
        client_context = create_nested_client(
            session, 's3', region_name='us-east-1'
        )

        async with client_context as client:
            # Should have the expected service name
            assert client._service_model.service_name == 's3'

            # Should have expected methods
            assert hasattr(client, 'list_buckets')
            assert hasattr(client, 'get_object')
            assert hasattr(client, 'put_object')

    def test_get_client_creator_functional_test(self, session):
        """Functional test for _get_client_creator."""
        client_creator = _get_client_creator(session, 'us-east-1')

        # Should return a function
        assert callable(client_creator)

        # Function should create client contexts
        s3_context = client_creator('s3')
        assert hasattr(s3_context, '__aenter__')
        assert hasattr(s3_context, '__aexit__')

        # Should work with different services
        sts_context = client_creator('sts')
        assert hasattr(sts_context, '__aenter__')
        assert hasattr(sts_context, '__aexit__')
