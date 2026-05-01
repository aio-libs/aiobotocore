from __future__ import annotations

import itertools
import json
import unittest
from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest import mock

import pytest
from botocore.endpoint_provider import RuleSetEndpoint
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    InvalidRegionError,
    ReadTimeoutError,
)
from botocore.utils import BadIMDSRequestError, MetadataRetrievalError

from aiobotocore import utils
from aiobotocore.awsrequest import AioAWSResponse
from aiobotocore.regions import AioEndpointRulesetResolver
from aiobotocore.utils import (
    AioInstanceMetadataFetcher,
    AioS3RegionRedirectorv2,
)
from tests.test_response import AsyncBytesIO


class TestS3RegionRedirector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = mock.AsyncMock()
        self.client._ruleset_resolver = AioEndpointRulesetResolver(
            endpoint_ruleset_data={
                'version': '1.0',
                'parameters': {},
                'rules': [],
            },
            partition_data={},
            service_model=None,
            builtins={},
            client_context=None,
            event_emitter=None,
            use_ssl=True,
            requested_auth_scheme=None,
        )
        self.client._ruleset_resolver.construct_endpoint = mock.AsyncMock(
            return_value=RuleSetEndpoint(
                url='https://new-endpoint.amazonaws.com',
                properties={},
                headers={},
            )
        )
        self.cache = {}
        self.redirector = AioS3RegionRedirectorv2(None, self.client)
        self.set_client_response_headers({})
        self.operation = mock.Mock()
        self.operation.name = 'foo'

    def set_client_response_headers(self, headers):
        error_response = ClientError(
            {
                'Error': {'Code': '', 'Message': ''},
                'ResponseMetadata': {'HTTPHeaders': headers},
            },
            'HeadBucket',
        )
        success_response = {'ResponseMetadata': {'HTTPHeaders': headers}}
        self.client.head_bucket.side_effect = [
            error_response,
            success_response,
        ]

    def test_set_request_url(self):
        old_url = 'https://us-west-2.amazonaws.com/foo'
        new_endpoint = 'https://eu-central-1.amazonaws.com'
        new_url = self.redirector.set_request_url(old_url, new_endpoint)
        self.assertEqual(new_url, 'https://eu-central-1.amazonaws.com/foo')

    def test_set_request_url_keeps_old_scheme(self):
        old_url = 'http://us-west-2.amazonaws.com/foo'
        new_endpoint = 'https://eu-central-1.amazonaws.com'
        new_url = self.redirector.set_request_url(old_url, new_endpoint)
        self.assertEqual(new_url, 'http://eu-central-1.amazonaws.com/foo')

    def test_sets_signing_context_from_cache(self):
        self.cache['foo'] = 'new-region-1'
        self.redirector = AioS3RegionRedirectorv2(
            None, self.client, cache=self.cache
        )
        params = {'Bucket': 'foo'}
        builtins = {'AWS::Region': 'old-region-1'}
        self.redirector.redirect_from_cache(builtins, params)
        self.assertEqual(builtins.get('AWS::Region'), 'new-region-1')

    def test_only_changes_context_if_bucket_in_cache(self):
        self.cache['foo'] = 'new-region-1'
        self.redirector = AioS3RegionRedirectorv2(
            None, self.client, cache=self.cache
        )
        params = {'Bucket': 'bar'}
        builtins = {'AWS::Region': 'old-region-1'}
        self.redirector.redirect_from_cache(builtins, params)
        self.assertEqual(builtins.get('AWS::Region'), 'old-region-1')

    async def test_redirect_from_error(self):
        request_dict = {
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {'Bucket': 'foo'},
                },
                'signing': {
                    'region': 'us-west-2',
                },
            },
            'url': 'https://us-west-2.amazonaws.com/foo',
        }
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                },
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.client._ruleset_resolver.construct_endpoint.return_value = (
            RuleSetEndpoint(
                url='https://eu-central-1.amazonaws.com/foo',
                properties={
                    'authSchemes': [
                        {
                            'name': 'sigv4',
                            'signingRegion': 'eu-central-1',
                            'disableDoubleEncoding': True,
                        }
                    ]
                },
                headers={},
            )
        )

        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )

        # The response needs to be 0 so that there is no retry delay
        self.assertEqual(redirect_response, 0)

        self.assertEqual(
            request_dict['url'], 'https://eu-central-1.amazonaws.com/foo'
        )

        expected_signing_context = {
            'region': 'eu-central-1',
            'disableDoubleEncoding': True,
        }
        signing_context = request_dict['context'].get('signing')
        self.assertEqual(signing_context, expected_signing_context)
        self.assertTrue(
            request_dict['context']['s3_redirect'].get('redirected')
        )

    async def test_does_not_redirect_if_previously_redirected(self):
        request_dict = {
            'context': {
                'signing': {'bucket': 'foo', 'region': 'us-west-2'},
                's3_redirected': True,
            },
            'url': 'https://us-west-2.amazonaws.com/foo',
        }
        response = (
            None,
            {
                'Error': {
                    'Code': '400',
                    'Message': 'Bad Request',
                },
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'us-west-2'}
                },
            },
        )
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)

    async def test_does_not_redirect_unless_permanentredirect_recieved(self):
        request_dict = {}
        response = (None, {})
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)
        self.assertEqual(request_dict, {})

    async def test_does_not_redirect_if_region_cannot_be_found(self):
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {'Bucket': 'foo'},
                },
                'signing': {},
            },
        }
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                },
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )

        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )

        self.assertIsNone(redirect_response)

    async def test_redirects_301(self):
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {'Bucket': 'foo'},
                },
                'signing': {},
            },
        }
        response = (
            None,
            {
                'Error': {'Code': '301', 'Message': 'Moved Permanently'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.operation.name = 'HeadObject'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertEqual(redirect_response, 0)

        self.operation.name = 'ListObjects'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)

    async def test_redirects_400_head_bucket(self):
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {'Bucket': 'foo'},
                },
                'signing': {},
            },
        }
        response = (
            None,
            {
                'Error': {'Code': '400', 'Message': 'Bad Request'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.operation.name = 'HeadObject'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertEqual(redirect_response, 0)

        self.operation.name = 'ListObjects'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)

    async def test_does_not_redirect_400_head_bucket_no_region_header(self):
        # We should not redirect a 400 Head* if the region header is not
        # present as this will lead to infinitely calling HeadBucket.
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {'signing': {'bucket': 'foo'}},
        }
        response = (
            None,
            {
                'Error': {'Code': '400', 'Message': 'Bad Request'},
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )

        self.operation.name = 'HeadBucket'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        head_bucket_calls = self.client.head_bucket.call_count
        self.assertIsNone(redirect_response)
        # We should not have made an additional head bucket call
        self.assertEqual(head_bucket_calls, 0)

    async def test_does_not_redirect_if_None_response(self):
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {'signing': {'bucket': 'foo'}},
        }
        response = None
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)

    async def test_redirects_on_illegal_location_constraint_from_opt_in_region(
        self,
    ):
        request_dict = {
            'url': 'https://il-central-1.amazonaws.com/foo',
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {'Bucket': 'foo'},
                },
                'signing': {},
            },
        }
        response = (
            None,
            {
                'Error': {'Code': 'IllegalLocationConstraintException'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.operation.name = 'GetObject'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertEqual(redirect_response, 0)

    async def test_no_redirect_on_illegal_location_constraint_from_bad_location_constraint(
        self,
    ):
        request_dict = {
            'url': 'https://us-west-2.amazonaws.com/foo',
            'context': {
                's3_redirect': {
                    'bucket': 'foo',
                    'redirected': False,
                    'params': {
                        'Bucket': 'foo',
                        'CreateBucketConfiguration': {
                            'LocationConstraint': 'eu-west-2',
                        },
                    },
                },
                'signing': {},
            },
        }
        response = (
            None,
            {
                'Error': {'Code': 'IllegalLocationConstraintException'},
            },
        )

        self.operation.name = 'CreateBucket'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertIsNone(redirect_response)

    async def test_get_region_from_response(self):
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                },
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )
        region = await self.redirector.get_bucket_region('foo', response)
        self.assertEqual(region, 'eu-central-1')

    async def test_get_region_from_response_error_body(self):
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                    'Region': 'eu-central-1',
                },
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )
        region = await self.redirector.get_bucket_region('foo', response)
        self.assertEqual(region, 'eu-central-1')

    async def test_get_region_from_head_bucket_error(self):
        self.set_client_response_headers(
            {'x-amz-bucket-region': 'eu-central-1'}
        )
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                },
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )
        region = await self.redirector.get_bucket_region('foo', response)
        self.assertEqual(region, 'eu-central-1')

    async def test_get_region_from_head_bucket_success(self):
        success_response = {
            'ResponseMetadata': {
                'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
            }
        }
        self.client.head_bucket.side_effect = None
        self.client.head_bucket.return_value = success_response
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Endpoint': 'foo.eu-central-1.amazonaws.com',
                    'Bucket': 'foo',
                },
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )
        region = await self.redirector.get_bucket_region('foo', response)
        self.assertEqual(region, 'eu-central-1')

    async def test_no_redirect_from_error_for_accesspoint(self):
        request_dict = {
            'url': (
                'https://myendpoint-123456789012.s3-accesspoint.'
                'us-west-2.amazonaws.com/key'
            ),
            'context': {
                's3_redirect': {
                    'redirected': False,
                    'bucket': 'arn:aws:s3:us-west-2:123456789012:myendpoint',
                    'params': {},
                }
            },
        }
        response = (
            None,
            {
                'Error': {'Code': '400', 'Message': 'Bad Request'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.operation.name = 'HeadObject'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertEqual(redirect_response, None)

    async def test_no_redirect_from_error_for_mrap_accesspoint(self):
        mrap_arn = 'arn:aws:s3::123456789012:accesspoint:mfzwi23gnjvgw.mrap'
        request_dict = {
            'url': (
                'https://mfzwi23gnjvgw.mrap.accesspoint.'
                's3-global.amazonaws.com'
            ),
            'context': {
                's3_redirect': {
                    'redirected': False,
                    'bucket': mrap_arn,
                    'params': {},
                }
            },
        }
        response = (
            None,
            {
                'Error': {'Code': '400', 'Message': 'Bad Request'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'eu-central-1'}
                },
            },
        )

        self.operation.name = 'HeadObject'
        redirect_response = await self.redirector.redirect_from_error(
            request_dict, response, self.operation
        )
        self.assertEqual(redirect_response, None)

    async def test_get_region_validates_region_from_header(self):
        response = (
            None,
            {
                'Error': {'Code': 'PermanentRedirect'},
                'ResponseMetadata': {
                    'HTTPHeaders': {'x-amz-bucket-region': 'invalid region!'}
                },
            },
        )
        with self.assertRaises(InvalidRegionError):
            await self.redirector.get_bucket_region('foo', response)

    async def test_get_region_validates_region_from_error_body(self):
        response = (
            None,
            {
                'Error': {
                    'Code': 'PermanentRedirect',
                    'Region': 'invalid region!',
                },
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )
        with self.assertRaises(InvalidRegionError):
            await self.redirector.get_bucket_region('foo', response)

    async def test_get_region_validates_region_from_head_bucket(self):
        self.set_client_response_headers(
            {'x-amz-bucket-region': 'invalid region!'}
        )
        response = (
            None,
            {
                'Error': {'Code': 'PermanentRedirect'},
                'ResponseMetadata': {'HTTPHeaders': {}},
            },
        )
        with self.assertRaises(InvalidRegionError):
            await self.redirector.get_bucket_region('foo', response)


Response = tuple[str | object, int]


# From class TestContainerMetadataFetcher
def fake_aiohttp_session(responses: list[Response] | Response):
    """
    Dodgy shim class
    """
    if isinstance(responses, tuple):
        data: Iterator[Response] = itertools.cycle([responses])
    else:
        data = iter(responses)

    class FakeAioHttpSession:
        @asynccontextmanager
        async def acquire(self):
            yield self

        class FakeResponse:
            def __init__(self, request, *args, **kwargs):
                self.request = request
                self.url = request.url
                self._body, self.status_code = next(data)
                self.content = self._content()
                self.text = self._text()
                if not isinstance(self._body, str):
                    raise self._body

            async def _content(self):
                return self._body.encode('utf-8')

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def _text(self):
                return self._body

            async def json(self):
                return json.loads(self._body)

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def send(self, request):
            return self.FakeResponse(request)

    return FakeAioHttpSession()


async def test_idmsfetcher_disabled():
    env = {'AWS_EC2_METADATA_DISABLED': 'true'}
    fetcher = utils.AioIMDSFetcher(env=env)

    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


async def test_idmsfetcher_get_token_success():
    session = fake_aiohttp_session(
        ('blah', 200),
    )

    fetcher = utils.AioIMDSFetcher(
        num_attempts=2, session=session, user_agent='test'
    )
    response = await fetcher._fetch_metadata_token()
    assert response == 'blah'


async def test_idmsfetcher_get_token_not_found():
    session = fake_aiohttp_session(
        ('blah', 404),
    )

    fetcher = utils.AioIMDSFetcher(
        num_attempts=2, session=session, user_agent='test'
    )
    response = await fetcher._fetch_metadata_token()
    assert response is None


async def test_idmsfetcher_get_token_bad_request():
    session = fake_aiohttp_session(
        ('blah', 400),
    )

    fetcher = utils.AioIMDSFetcher(
        num_attempts=2, session=session, user_agent='test'
    )
    with pytest.raises(BadIMDSRequestError):
        await fetcher._fetch_metadata_token()


async def test_idmsfetcher_get_token_timeout():
    session = fake_aiohttp_session(
        [
            (ReadTimeoutError(endpoint_url='aaa'), 500),
        ]
    )

    fetcher = utils.AioIMDSFetcher(num_attempts=2, session=session)

    response = await fetcher._fetch_metadata_token()
    assert response is None


async def test_idmsfetcher_get_token_retry():
    session = fake_aiohttp_session(
        [
            ('blah', 500),
            ('blah', 500),
            ('token', 200),
        ]
    )

    fetcher = utils.AioIMDSFetcher(num_attempts=3, session=session)

    response = await fetcher._fetch_metadata_token()
    assert response == 'token'


async def test_idmsfetcher_retry():
    session = fake_aiohttp_session(
        [
            ('blah', 500),
            ('data', 200),
        ]
    )

    fetcher = utils.AioIMDSFetcher(
        num_attempts=2, session=session, user_agent='test'
    )
    response = await fetcher._get_request('path', None, 'some_token')

    assert await response.text == 'data'

    session = fake_aiohttp_session(
        [
            ('blah', 500),
            ('data', 200),
        ]
    )

    fetcher = utils.AioIMDSFetcher(num_attempts=1, session=session)
    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


async def test_idmsfetcher_timeout():
    session = fake_aiohttp_session(
        [
            (ReadTimeoutError(endpoint_url='url'), 500),
        ]
    )

    fetcher = utils.AioIMDSFetcher(num_attempts=1, session=session)

    with pytest.raises(fetcher._RETRIES_EXCEEDED_ERROR_CLS):
        await fetcher._get_request('path', None)


class TestInstanceMetadataFetcher(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        urllib3_session_send = 'aiobotocore.httpsession.AIOHTTPSession.send'
        self._urllib3_patch = mock.patch(urllib3_session_send)
        self._send = self._urllib3_patch.start()
        self._imds_responses = []
        self._send.side_effect = self.get_imds_response
        self._role_name = 'role-name'
        self._creds = {
            'AccessKeyId': 'spam',
            'SecretAccessKey': 'eggs',
            'Token': 'spam-token',
            'Expiration': 'something',
        }
        self._expected_creds = {
            'access_key': self._creds['AccessKeyId'],
            'secret_key': self._creds['SecretAccessKey'],
            'token': self._creds['Token'],
            'expiry_time': self._creds['Expiration'],
            'role_name': self._role_name,
        }

    async def asyncTearDown(self):
        self._urllib3_patch.stop()

    def add_imds_response(self, body, status_code=200):
        response = AioAWSResponse(
            url='http://169.254.169.254/',
            status_code=status_code,
            headers={},
            raw=AsyncBytesIO(body),
        )

        self._imds_responses.append(response)

    def add_get_role_name_imds_response(self, role_name=None):
        if role_name is None:
            role_name = self._role_name
        self.add_imds_response(body=role_name.encode('utf-8'))

    def add_get_credentials_imds_response(self, creds=None):
        if creds is None:
            creds = self._creds
        self.add_imds_response(body=json.dumps(creds).encode('utf-8'))

    def add_get_token_imds_response(self, token, status_code=200):
        self.add_imds_response(
            body=token.encode('utf-8'), status_code=status_code
        )

    def add_metadata_token_not_supported_response(self):
        self.add_imds_response(b'', status_code=404)

    def add_imds_connection_error(self, exception):
        self._imds_responses.append(exception)

    def get_imds_response(self, *args, **kwargs):
        response = self._imds_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def test_disabled_by_environment(self):
        env = {'AWS_EC2_METADATA_DISABLED': 'true'}
        fetcher = AioInstanceMetadataFetcher(env=env)
        result = await fetcher.retrieve_iam_role_credentials()
        self.assertEqual(result, {})
        self._send.assert_not_called()

    async def test_disabled_by_environment_mixed_case(self):
        env = {'AWS_EC2_METADATA_DISABLED': 'tRuE'}
        fetcher = AioInstanceMetadataFetcher(env=env)
        result = await fetcher.retrieve_iam_role_credentials()
        self.assertEqual(result, {})
        self._send.assert_not_called()

    async def test_disabling_env_var_not_true(self):
        url = 'https://example.com/'
        env = {'AWS_EC2_METADATA_DISABLED': 'false'}

        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        fetcher = AioInstanceMetadataFetcher(base_url=url, env=env)
        result = await fetcher.retrieve_iam_role_credentials()

        self.assertEqual(result, self._expected_creds)

    async def test_includes_user_agent_header(self):
        user_agent = 'my-user-agent'
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        self.assertEqual(self._send.call_count, 3)
        for call in self._send.calls:
            self.assertTrue(call[0][0].headers['User-Agent'], user_agent)

    async def test_non_200_response_for_role_name_is_retried(self):
        # Response for role name that have a non 200 status code should
        # be retried.
        self.add_get_token_imds_response(token='token')
        self.add_imds_response(
            status_code=429, body=b'{"message": "Slow down"}'
        )
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_http_connection_error_for_role_name_is_retried(self):
        # Connection related errors should be retried
        self.add_get_token_imds_response(token='token')
        self.add_imds_connection_error(ConnectionClosedError(endpoint_url=''))
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_empty_response_for_role_name_is_retried(self):
        # Response for role name that have a non 200 status code should
        # be retried.
        self.add_get_token_imds_response(token='token')
        self.add_imds_response(body=b'')
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_non_200_response_is_retried(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        # Response for creds that has a 200 status code but has an empty
        # body should be retried.
        self.add_imds_response(
            status_code=429, body=b'{"message": "Slow down"}'
        )
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_http_connection_errors_is_retried(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        # Connection related errors should be retried
        self.add_imds_connection_error(ConnectionClosedError(endpoint_url=''))
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_empty_response_is_retried(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        # Response for creds that has a 200 status code but is empty.
        # This should be retried.
        self.add_imds_response(body=b'')
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_invalid_json_is_retried(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        # Response for creds that has a 200 status code but is invalid JSON.
        # This should be retried.
        self.add_imds_response(body=b'{"AccessKey":')
        self.add_get_credentials_imds_response()
        result = await AioInstanceMetadataFetcher(
            num_attempts=2
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, self._expected_creds)

    async def test_exhaust_retries_on_role_name_request(self):
        self.add_get_token_imds_response(token='token')
        self.add_imds_response(status_code=400, body=b'')
        result = await AioInstanceMetadataFetcher(
            num_attempts=1
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, {})

    async def test_exhaust_retries_on_credentials_request(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        self.add_imds_response(status_code=400, body=b'')
        result = await AioInstanceMetadataFetcher(
            num_attempts=1
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, {})

    async def test_missing_fields_in_credentials_response(self):
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        # Response for creds that has a 200 status code and a JSON body
        # representing an error. We do not necessarily want to retry this.
        self.add_imds_response(
            body=b'{"Code":"AssumeRoleUnauthorizedAccess","Message":"error"}'
        )
        result = (
            await AioInstanceMetadataFetcher().retrieve_iam_role_credentials()
        )
        self.assertEqual(result, {})

    async def test_token_is_included(self):
        user_agent = 'my-user-agent'
        self.add_get_token_imds_response(token='token')
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        # Check that subsequent calls after getting the token include the token.
        self.assertEqual(self._send.call_count, 3)
        for call in self._send.call_args_list[1:]:
            self.assertEqual(
                call[0][0].headers['x-aws-ec2-metadata-token'], 'token'
            )
        self.assertEqual(result, self._expected_creds)

    async def test_metadata_token_not_supported_404(self):
        user_agent = 'my-user-agent'
        self.add_imds_response(b'', status_code=404)
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        for call in self._send.call_args_list[1:]:
            self.assertNotIn('x-aws-ec2-metadata-token', call[0][0].headers)
        self.assertEqual(result, self._expected_creds)

    async def test_metadata_token_not_supported_403(self):
        user_agent = 'my-user-agent'
        self.add_imds_response(b'', status_code=403)
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        for call in self._send.call_args_list[1:]:
            self.assertNotIn('x-aws-ec2-metadata-token', call[0][0].headers)
        self.assertEqual(result, self._expected_creds)

    async def test_metadata_token_not_supported_405(self):
        user_agent = 'my-user-agent'
        self.add_imds_response(b'', status_code=405)
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        for call in self._send.call_args_list[1:]:
            self.assertNotIn('x-aws-ec2-metadata-token', call[0][0].headers)
        self.assertEqual(result, self._expected_creds)

    async def test_metadata_token_not_supported_timeout(self):
        user_agent = 'my-user-agent'
        self.add_imds_connection_error(ReadTimeoutError(endpoint_url='url'))
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        for call in self._send.call_args_list[1:]:
            self.assertNotIn('x-aws-ec2-metadata-token', call[0][0].headers)
        self.assertEqual(result, self._expected_creds)

    async def test_token_not_supported_exhaust_retries(self):
        user_agent = 'my-user-agent'
        self.add_imds_connection_error(ConnectTimeoutError(endpoint_url='url'))
        self.add_get_role_name_imds_response()
        self.add_get_credentials_imds_response()

        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()

        for call in self._send.call_args_list[1:]:
            self.assertNotIn('x-aws-ec2-metadata-token', call[0][0].headers)
        self.assertEqual(result, self._expected_creds)

    async def test_metadata_token_bad_request_yields_no_credentials(self):
        user_agent = 'my-user-agent'
        self.add_imds_response(b'', status_code=400)
        result = await AioInstanceMetadataFetcher(
            user_agent=user_agent
        ).retrieve_iam_role_credentials()
        self.assertEqual(result, {})


async def test_containermetadatafetcher_retrieve_url():
    json_body = json.dumps(
        {
            "AccessKeyId": "a",
            "SecretAccessKey": "b",
            "Token": "c",
            "Expiration": "d",
        }
    )

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 200))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    resp = await fetcher.retrieve_uri('/foo?id=1')
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'

    resp = await fetcher.retrieve_full_uri(
        'http://localhost/foo?id=1', {'extra': 'header'}
    )
    assert resp['AccessKeyId'] == 'a'
    assert resp['SecretAccessKey'] == 'b'
    assert resp['Token'] == 'c'
    assert resp['Expiration'] == 'd'


async def test_containermetadatafetcher_retrieve_url_bad_status():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 500))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')


async def test_containermetadatafetcher_retrieve_url_not_json():
    json_body = "not json"

    sleep = mock.AsyncMock()
    http = fake_aiohttp_session((json_body, 200))

    fetcher = utils.AioContainerMetadataFetcher(http, sleep)
    with pytest.raises(MetadataRetrievalError):
        await fetcher.retrieve_uri('/foo?id=1')
