import copy
import inspect

import botocore.client
from botocore.exceptions import ParamValidationError


class AioConfig(botocore.client.Config):

    def __init__(self, connector_args=None, **kwargs):
        super().__init__(**kwargs)

        self._validate_connector_args(connector_args)
        self.connector_args = copy.copy(connector_args)
        if not self.connector_args:
            self.connector_args = dict()

        if 'keepalive_timeout' not in self.connector_args:
            # AWS has a 20 second idle timeout:
            # https://forums.aws.amazon.com/message.jspa?messageID=215367
            # and aiohttp default timeout is 30s so we set it to something
            # reasonable here
            self.connector_args['keepalive_timeout'] = 12

    def merge(self, other_config):
        # Adapted from parent class
        config_options = copy.copy(self._user_provided_options)
        config_options.update(other_config._user_provided_options)
        return AioConfig(self.connector_args, **config_options)

    @staticmethod
    def _validate_connector_args(connector_args):
        if connector_args is None:
            return

        for k, v in connector_args.items():
            # verify_ssl is handled by verify parameter to create_client
            if k == 'use_dns_cache':
                if not isinstance(v, bool):
                    raise ParamValidationError(
                        report='{} value must be a boolean'.format(k))
            elif k in ['keepalive_timeout']:
                if not isinstance(v, (float, int)):
                    raise ParamValidationError(
                        report='{} value must be a float/int'.format(k))
            elif k == 'force_close':
                if not isinstance(v, bool):
                    raise ParamValidationError(
                        report='{} value must be a boolean'.format(k))
            # limit is handled by max_pool_connections
            elif k == 'ssl_context':
                import ssl
                if not isinstance(v, ssl.SSLContext):
                    raise ParamValidationError(
                        report='{} must be an SSLContext instance'.format(k))
            elif k == "resolver":
                from aiohttp.abc import AbstractResolver
                if not inspect.isclass(v) or not issubclass(v, AbstractResolver):
                    raise ParamValidationError(
                        report='{} must be an AbstractResolver subclass'.format(k))
            else:
                raise ParamValidationError(
                    report='invalid connector_arg:{}'.format(k))
