import asyncio
import botocore.credentials
import botocore.session
from botocore import retryhandler, translate

from .client import AioClientCreator


class AioSession(botocore.session.Session):

    def __init__(self, session_vars=None, event_hooks=None,
                 include_builtin_handlers=True, loader=None, loop=None):

        super().__init__(session_vars=session_vars, event_hooks=event_hooks,
                         include_builtin_handlers=include_builtin_handlers)
        self._loop = loop
        self._loader = loader

    def create_client(self, service_name, region_name=None, api_version=None,
                      use_ssl=True, verify=None, endpoint_url=None,
                      aws_access_key_id=None, aws_secret_access_key=None,
                      aws_session_token=None, config=None):

        if region_name is None:
            if config and config.region_name is not None:
                region_name = config.region_name
            else:
                region_name = self.get_config_variable('region')
        loader = self.get_component('data_loader')
        event_emitter = self.get_component('event_emitter')
        response_parser_factory = self.get_component(
            'response_parser_factory')
        if aws_secret_access_key is not None:
            credentials = botocore.credentials.Credentials(
                access_key=aws_access_key_id,
                secret_key=aws_secret_access_key,
                token=aws_session_token)
        else:
            credentials = self.get_credentials()
        endpoint_resolver = self.get_component('endpoint_resolver')
        client_creator = AioClientCreator(
            loader, endpoint_resolver, self.user_agent(), event_emitter,
            retryhandler, translate, response_parser_factory, loop=self._loop)
        client = client_creator.create_client(
            service_name, region_name, use_ssl, endpoint_url, verify,
            credentials, scoped_config=self.get_scoped_config(),
            client_config=config, api_version=api_version)
        return client


def get_session(*, env_vars=None, loop=None):
    """
    Return a new session object.
    """
    loop = loop or asyncio.get_event_loop()
    return AioSession(session_vars=env_vars, loop=loop)
