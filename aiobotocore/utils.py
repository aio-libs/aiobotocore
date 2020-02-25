import asyncio
import logging
import json

import aiohttp
import aiohttp.client_exceptions
from botocore.utils import ContainerMetadataFetcher, InstanceMetadataFetcher, \
    IMDSFetcher, get_environ_proxies
from botocore.exceptions import MetadataRetrievalError


logger = logging.getLogger(__name__)
RETRYABLE_HTTP_ERRORS = (aiohttp.client_exceptions.ClientError, asyncio.TimeoutError)


class AioContainerMetadataFetcher(ContainerMetadataFetcher):

    TIMEOUT_SECONDS = 2
    RETRY_ATTEMPTS = 3
    SLEEP_TIME = 1
    IP_ADDRESS = '169.254.170.2'
    _ALLOWED_HOSTS = [IP_ADDRESS, 'localhost', '127.0.0.1']

    def __init__(self, *args, **kwargs):
        super(AioContainerMetadataFetcher, self).__init__(*args, **kwargs)
        self._sleep = asyncio.sleep

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
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(full_url, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise MetadataRetrievalError(
                            error_msg=(
                                          "Received non 200 response (%s) "
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


class AioIMDSFetcher(IMDSFetcher):
    class Response(object):
        def __init__(self, status_code, text):
            self.status_code = status_code,
            self.text = text
            self.content = text

    def __init__(self, *args, **kwargs):
        super(AioIMDSFetcher, self).__init__(*args, **kwargs)
        self._trust_env = get_environ_proxies(self._base_url)

    async def _get_request(self, url_path, retry_func):
        if self._disabled:
            logger.debug("Access to EC2 metadata has been disabled.")
            raise self._RETRIES_EXCEEDED_ERROR_CLS()
        if retry_func is None:
            retry_func = self._default_retry

        url = self._base_url + url_path
        headers = {}
        if self._user_agent is not None:
            headers['User-Agent'] = self._user_agent

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=timeout,
                                         trust_env=self._trust_env) as session:
            for i in range(self._num_attempts):
                try:
                    async with session.get(url, headers=headers) as resp:
                        text = await resp.text()
                        response = self.Response(resp.status, text)

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
            role_name = await self._get_iam_role()
            credentials = await self._get_credentials(role_name)
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
        return {}

    async def _get_iam_role(self):
        r = await self._get_request(
            url_path=self._URL_PATH,
            retry_func=self._needs_retry_for_role_name
        )
        return r.text

    async def _get_credentials(self, role_name):
        r = await self._get_request(
            url_path=self._URL_PATH + role_name,
            retry_func=self._needs_retry_for_credentials
        )
        return json.loads(r.text)
