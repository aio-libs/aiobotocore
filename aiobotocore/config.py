import copy
import ssl
from concurrent.futures import Executor
from typing import Optional, TypedDict, Union

import botocore.client
from aiohttp.abc import AbstractResolver
from botocore.exceptions import ParamValidationError
from typing_extensions import NotRequired

from ._constants import DEFAULT_KEEPALIVE_TIMEOUT
from .endpoint import DEFAULT_HTTP_SESSION_CLS
from .httpsession import AIOHTTPSession
from .httpxsession import HttpxSession

TIMEOUT_ARGS = frozenset(
    ('keepalive_timeout', 'write_timeout', 'pool_timeout')
)


class _ConnectorArgsType(TypedDict):
    use_dns_cache: NotRequired[bool]
    ttl_dns_cache: NotRequired[Optional[int]]
    keepalive_timeout: NotRequired[Union[float, int, None]]
    write_timeout: NotRequired[Union[float, int, None]]
    pool_timeout: NotRequired[Union[float, int, None]]
    force_close: NotRequired[bool]
    ssl_context: NotRequired[ssl.SSLContext]
    resolver: NotRequired[AbstractResolver]


_HttpSessionTypes = Union[AIOHTTPSession, HttpxSession]


class AioConfig(botocore.client.Config):
    def __init__(
        self,
        connector_args: Optional[_ConnectorArgsType] = None,
        http_session_cls: _HttpSessionTypes = DEFAULT_HTTP_SESSION_CLS,
        load_executor: Optional[Executor] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self._validate_connector_args(connector_args, http_session_cls)

        if load_executor and not isinstance(load_executor, Executor):
            raise ParamValidationError(
                report='load_executor value must be an instance of an Executor.'
            )

        self.load_executor = load_executor
        self.connector_args = (
            copy.copy(connector_args) if connector_args else dict()
        )
        self.http_session_cls = http_session_cls

        if 'keepalive_timeout' not in self.connector_args:
            self.connector_args['keepalive_timeout'] = (
                DEFAULT_KEEPALIVE_TIMEOUT
            )

    def merge(self, other_config):
        # Adapted from parent class
        config_options = copy.copy(self._user_provided_options)
        config_options.update(other_config._user_provided_options)
        return AioConfig(self.connector_args, **config_options)

    @staticmethod
    def _validate_connector_args(
        connector_args: _ConnectorArgsType, http_session_cls: _HttpSessionTypes
    ):
        if connector_args is None:
            return

        for k, v in connector_args.items():
            # verify_ssl is handled by verify parameter to create_client
            if k == 'use_dns_cache':
                if http_session_cls is HttpxSession:
                    raise ParamValidationError(
                        report='Httpx does not support dns caching. https://github.com/encode/httpx/discussions/2211'
                    )
                if not isinstance(v, bool):
                    raise ParamValidationError(
                        report=f'{k} value must be a boolean'
                    )
            elif k == 'ttl_dns_cache':
                if v is not None and not isinstance(v, int):
                    raise ParamValidationError(
                        report=f'{k} value must be an int or None'
                    )
            elif k in TIMEOUT_ARGS:
                if v is not None and not isinstance(v, (float, int)):
                    raise ParamValidationError(
                        report=f'{k} value must be a float/int or None'
                    )
            elif k == 'force_close':
                if http_session_cls is HttpxSession:
                    raise ParamValidationError(
                        report=f'Httpx backend does not currently support {k}.'
                    )
                if not isinstance(v, bool):
                    raise ParamValidationError(
                        report=f'{k} value must be a boolean'
                    )
            # limit is handled by max_pool_connections
            elif k == 'ssl_context':
                import ssl

                if not isinstance(v, ssl.SSLContext):
                    raise ParamValidationError(
                        report=f'{k} must be an SSLContext instance'
                    )
            elif k == "resolver":
                if http_session_cls is HttpxSession:
                    raise ParamValidationError(
                        report=f'Httpx backend does not support {k}.'
                    )
                if not isinstance(v, AbstractResolver):
                    raise ParamValidationError(
                        report=f'{k} must be an instance of a AbstractResolver'
                    )
            else:
                raise ParamValidationError(report=f'invalid connector_arg:{k}')
