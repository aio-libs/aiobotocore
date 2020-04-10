import asyncio
import logging
import json

import aiohttp
import aiohttp.client_exceptions
from botocore.utils import ContainerMetadataFetcher, InstanceMetadataFetcher, \
    IMDSFetcher, get_environ_proxies, BadIMDSRequestError
from botocore.exceptions import MetadataRetrievalError
import botocore.awsrequest


logger = logging.getLogger(__name__)
RETRYABLE_HTTP_ERRORS = (aiohttp.client_exceptions.ClientError, asyncio.TimeoutError)


class AioIMDSFetcher(IMDSFetcher):
    class Response(object):
        def __init__(self, status_code, text, url):
            self.status_code = status_code
            self.url = url
            self.text = text
            self.content = text

    def __init__(self, *args, session=None, **kwargs):
        super(AioIMDSFetcher, self).__init__(*args, **kwargs)
        self._trust_env = bool(get_environ_proxies(self._base_url))
        self._session = session or aiohttp.ClientSession

    async def _fetch_metadata_token(self):
        self._assert_enabled()
        url = self._base_url + self._TOKEN_PATH
        headers = {
            'x-aws-ec2-metadata-token-ttl-seconds': self._TOKEN_TTL,
        }
        self._add_user_agent(headers)

        request = botocore.awsrequest.AWSRequest(
            method='PUT', url=url, headers=headers)

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with self._session(timeout=timeout,
                                 trust_env=self._trust_env) as session:
            for i in range(self._num_attempts):
                try:
                    async with session.put(url, headers=headers) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            return text
                        elif resp.status in (404, 403, 405):
                            return None
                        elif resp.status in (400,):
                            raise BadIMDSRequestError(request)
                except asyncio.TimeoutError:
                    return None
                except RETRYABLE_HTTP_ERRORS as e:
                    logger.debug(
                        "Caught retryable HTTP exception while making metadata "
                        "service request to %s: %s", url, e, exc_info=True)

        return None

    async def _get_request(self, url_path, retry_func, token=None):
        self._assert_enabled()
        if retry_func is None:
            retry_func = self._default_retry
        url = self._base_url + url_path
        headers = {}
        if token is not None:
            headers['x-aws-ec2-metadata-token'] = token
        self._add_user_agent(headers)

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with self._session(timeout=timeout,
                                 trust_env=self._trust_env) as session:
            for i in range(self._num_attempts):
                try:
                    async with session.get(url, headers=headers) as resp:
                        text = await resp.text()
                        response = self.Response(resp.status, text, resp.url)

                    if not retry_func(response):
                        return response
                except RETRYABLE_HTTP_ERRORS as e:
                    logger.debug(
                        "Caught retryable HTTP exception while making metadata "
                        "service request to %s: %s", url, e, exc_info=True)
        raise self._RETRIES_EXCEEDED_ERROR_CLS()


class AioInstanceMetadataFetcher(AioIMDSFetcher, InstanceMetadataFetcher):
    async def retrieve_iam_role_credentials(self):
        try:
            token = await self._fetch_metadata_token()
            role_name = await self._get_iam_role(token)
            credentials = await self._get_credentials(role_name, token)
            if self._contains_all_credential_fields(credentials):
                return {
                    'role_name': role_name,
                    'access_key': credentials['AccessKeyId'],
                    'secret_key': credentials['SecretAccessKey'],
                    'token': credentials['Token'],
                    'expiry_time': credentials['Expiration'],
                }
            else:
                if 'Code' in credentials and 'Message' in credentials:
                    logger.debug('Error response received when retrieving'
                                 'credentials: %s.', credentials)
                return {}
        except self._RETRIES_EXCEEDED_ERROR_CLS:
            logger.debug("Max number of attempts exceeded (%s) when "
                         "attempting to retrieve data from metadata service.",
                         self._num_attempts)
        except BadIMDSRequestError as e:
            logger.debug("Bad IMDS request: %s", e.request)
        return {}

    async def _get_iam_role(self, token=None):
        r = await self._get_request(
            url_path=self._URL_PATH,
            retry_func=self._needs_retry_for_role_name,
            token=token
        )
        return r.text

    async def _get_credentials(self, role_name, token=None):
        r = await self._get_request(
            url_path=self._URL_PATH + role_name,
            retry_func=self._needs_retry_for_credentials,
            token=token
        )
        return json.loads(r.text)


class AioContainerMetadataFetcher(ContainerMetadataFetcher):
    def __init__(self, session=None, sleep=asyncio.sleep):
        if session is None:
            session = aiohttp.ClientSession
        super(AioContainerMetadataFetcher, self).__init__(session, sleep)

    async def retrieve_full_uri(self, full_url, headers=None):
        self._validate_allowed_url(full_url)
        return await self._retrieve_credentials(full_url, headers)

    async def retrieve_uri(self, relative_uri):
        """Retrieve JSON metadata from ECS metadata.

        :type relative_uri: str
        :param relative_uri: A relative URI, e.g "/foo/bar?id=123"

        :return: The parsed JSON response.

        """
        full_url = self.full_url(relative_uri)
        return await self._retrieve_credentials(full_url)

    async def _retrieve_credentials(self, full_url, extra_headers=None):
        headers = {'Accept': 'application/json'}
        if extra_headers is not None:
            headers.update(extra_headers)
        attempts = 0
        while True:
            try:
                return await self._get_response(
                    full_url, headers, self.TIMEOUT_SECONDS)
            except MetadataRetrievalError as e:
                logger.debug("Received error when attempting to retrieve "
                             "container metadata: %s", e, exc_info=True)
                await self._sleep(self.SLEEP_TIME)
                attempts += 1
                if attempts >= self.RETRY_ATTEMPTS:
                    raise

    async def _get_response(self, full_url, headers, timeout):
        try:
            timeout = aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
            async with self._session(timeout=timeout) as session:
                async with session.get(full_url, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise MetadataRetrievalError(
                            error_msg=(
                                          "Received non 200 response (%d) "
                                          "from ECS metadata: %s"
                                      ) % (resp.status, text))
                    try:
                        return await resp.json()
                    except ValueError:
                        text = await resp.text()
                        error_msg = (
                            "Unable to parse JSON returned from ECS metadata services"
                        )
                        logger.debug('%s:%s', error_msg, text)
                        raise MetadataRetrievalError(error_msg=error_msg)
        except RETRYABLE_HTTP_ERRORS as e:
            error_msg = ("Received error when attempting to retrieve "
                         "ECS metadata: %s" % e)
            raise MetadataRetrievalError(error_msg=error_msg)
