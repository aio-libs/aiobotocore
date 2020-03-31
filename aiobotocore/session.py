from botocore.session import Session

from botocore import UNSIGNED
from botocore import retryhandler, translate
from botocore.exceptions import PartialCredentialsError
from .client import AioClientCreator, AioBaseClient
from .hooks import AioHierarchicalEmitter
from .parsers import AioResponseParserFactory
from .credentials import create_credential_resolver, AioCredentials


class ClientCreatorContext:
    def __init__(self, coro):
        self._coro = coro
        self._client = None

    async def __aenter__(self) -> AioBaseClient:
        self._client = await self._coro
        return await self._client.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.__aexit__(exc_type, exc_val, exc_tb)


class AioSession(Session):

    # noinspection PyMissingConstructor
    def __init__(self, session_vars=None, event_hooks=None,
                 include_builtin_handlers=True, profile=None):
        if event_hooks is None:
            event_hooks = AioHierarchicalEmitter()

        super().__init__(session_vars, event_hooks, include_builtin_handlers, profile)

    def _register_response_parser_factory(self):
        self._components.register_component('response_parser_factory',
                                            AioResponseParserFactory())

    def create_client(self, *args, **kwargs):
        return ClientCreatorContext(self._create_client(*args, **kwargs))

    async def _create_client(self, service_name, region_name=None,
                             api_version=None,
                             use_ssl=True, verify=None, endpoint_url=None,
                             aws_access_key_id=None, aws_secret_access_key=None,
                             aws_session_token=None, config=None):

        default_client_config = self.get_default_client_config()
        # If a config is provided and a default config is set, then
        # use the config resulting from merging the two.
        if config is not None and default_client_config is not None:
            config = default_client_config.merge(config)
        # If a config was not provided then use the default
        # client config from the session
        elif default_client_config is not None:
            config = default_client_config

        region_name = self._resolve_region_name(region_name, config)

        # Figure out the verify value base on the various
        # configuration options.
        if verify is None:
            verify = self.get_config_variable('ca_bundle')

        if api_version is None:
            api_version = self.get_config_variable('api_versions').get(
                service_name, None)

        loader = self.get_component('data_loader')
        event_emitter = self.get_component('event_emitter')
        response_parser_factory = self.get_component(
            'response_parser_factory')
        if config is not None and config.signature_version is UNSIGNED:
            credentials = None
        elif aws_access_key_id is not None and \
                aws_secret_access_key is not None:
            credentials = AioCredentials(
                access_key=aws_access_key_id,
                secret_key=aws_secret_access_key,
                token=aws_session_token)
        elif self._missing_cred_vars(aws_access_key_id,
                                     aws_secret_access_key):
            raise PartialCredentialsError(
                provider='explicit',
                cred_var=self._missing_cred_vars(aws_access_key_id,
                                                 aws_secret_access_key))
        else:
            credentials = await self.get_credentials()
        endpoint_resolver = self._get_internal_component('endpoint_resolver')
        exceptions_factory = self._get_internal_component('exceptions_factory')
        config_store = self.get_component('config_store')
        client_creator = AioClientCreator(
            loader, endpoint_resolver, self.user_agent(), event_emitter,
            retryhandler, translate, response_parser_factory,
            exceptions_factory, config_store)
        client = await client_creator.create_client(
            service_name=service_name, region_name=region_name,
            is_secure=use_ssl, endpoint_url=endpoint_url, verify=verify,
            credentials=credentials, scoped_config=self.get_scoped_config(),
            client_config=config, api_version=api_version)
        monitor = self._get_internal_component('monitor')
        if monitor is not None:
            monitor.register(client.meta.events)
        return client

    def _create_credential_resolver(self):
        return create_credential_resolver(
            self, region_name=self._last_client_region_used)

    async def get_credentials(self):
        if self._credentials is None:
            self._credentials = await (self._components.get_component(
                'credential_provider').load_credentials())
        return self._credentials


def get_session(env_vars=None):
    """
    Return a new session object.
    """
    return AioSession(env_vars)
