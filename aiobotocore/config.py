import collections.abc
import copy
import ssl
import sys
from typing import TypedDict, cast

import botocore.client
from aiohttp import SocketFactoryType
from aiohttp.abc import AbstractResolver
from botocore.exceptions import ParamValidationError

from ._constants import DEFAULT_KEEPALIVE_TIMEOUT
from .endpoint import DEFAULT_HTTP_SESSION_CLS
from .httpsession import AIOHTTPSession
from .httpxsession import HttpxSession, is_httpx_session_cls

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

TIMEOUT_ARGS = frozenset(
    ('keepalive_timeout', 'write_timeout', 'pool_timeout')
)


class _ConnectorArgs(TypedDict):
    use_dns_cache: NotRequired[bool]
    ttl_dns_cache: NotRequired[int | None]
    keepalive_timeout: NotRequired[float | None]
    write_timeout: NotRequired[float | None]
    pool_timeout: NotRequired[float | None]
    force_close: NotRequired[bool]
    ssl_context: NotRequired[ssl.SSLContext]
    resolver: NotRequired[AbstractResolver]
    socket_factory: NotRequired[SocketFactoryType | None]


_HttpSessionType = AIOHTTPSession | HttpxSession
_OPTION_DEFAULT = object()


class AioConfig(botocore.client.Config):
    def __init__(
        self,
        connector_args: _ConnectorArgs | None | object = _OPTION_DEFAULT,
        http_session_cls: type[_HttpSessionType] | object = _OPTION_DEFAULT,
        warm_up_loader_caches: bool | object = _OPTION_DEFAULT,
        **kwargs,
    ):
        aio_options = {}
        if connector_args is not _OPTION_DEFAULT:
            aio_options['connector_args'] = connector_args
        else:
            connector_args = None
        if http_session_cls is not _OPTION_DEFAULT:
            aio_options['http_session_cls'] = http_session_cls
        else:
            http_session_cls = DEFAULT_HTTP_SESSION_CLS
        if warm_up_loader_caches is not _OPTION_DEFAULT:
            aio_options['warm_up_loader_caches'] = warm_up_loader_caches
        else:
            warm_up_loader_caches = False

        super().__init__(**kwargs)
        self._user_provided_options.update(aio_options)

        self.connector_args: _ConnectorArgs = (
            copy.copy(cast(_ConnectorArgs, connector_args))
            if connector_args
            else {}
        )
        self.http_session_cls = cast(type[_HttpSessionType], http_session_cls)
        self.warm_up_loader_caches = cast(bool, warm_up_loader_caches)
        self._validate_connector_args(
            self.connector_args, self.http_session_cls
        )

        if 'keepalive_timeout' not in self.connector_args:
            self.connector_args['keepalive_timeout'] = (
                DEFAULT_KEEPALIVE_TIMEOUT
            )

    def merge(self, other_config):
        # Adapted from parent class
        config_options = copy.copy(self._user_provided_options)
        config_options.update(other_config._user_provided_options)
        return AioConfig(**config_options)

    @staticmethod
    def _validate_connector_args(
        connector_args: _ConnectorArgs,
        http_session_cls: type[_HttpSessionType],
    ) -> None:
        for k, v in connector_args.items():
            # verify_ssl is handled by verify parameter to create_client
            if k == 'use_dns_cache':
                if is_httpx_session_cls(http_session_cls):
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
                if is_httpx_session_cls(http_session_cls):
                    raise ParamValidationError(
                        report=f'Httpx backend does not currently support {k}.'
                    )
                if not isinstance(v, bool):
                    raise ParamValidationError(
                        report=f'{k} value must be a boolean'
                    )
            # limit is handled by max_pool_connections
            elif k == 'ssl_context':
                if not isinstance(v, ssl.SSLContext):
                    raise ParamValidationError(
                        report=f'{k} must be an SSLContext instance'
                    )
            elif k == "resolver":
                if is_httpx_session_cls(http_session_cls):
                    raise ParamValidationError(
                        report=f'Httpx backend does not support {k}.'
                    )
                if not isinstance(v, AbstractResolver):
                    raise ParamValidationError(
                        report=f'{k} must be an instance of a AbstractResolver'
                    )
            elif k == "socket_factory":
                if is_httpx_session_cls(http_session_cls):
                    raise ParamValidationError(
                        report=f'Httpx backend does not support {k}.'
                    )
                if v is not None and not isinstance(
                    v, collections.abc.Callable
                ):
                    raise ParamValidationError(
                        report=f'{k} must be a callable'
                    )
            else:
                raise ParamValidationError(report=f'invalid connector_arg:{k}')
