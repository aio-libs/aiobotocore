import asyncio
import copy
import botocore.args
import botocore.serialize
import botocore.parsers
from botocore.signers import RequestSigner

from .config import AioConfig
from .endpoint import AioEndpointCreator


class AioClientArgsCreator(botocore.args.ClientArgsCreator):
    def __init__(self, event_emitter, user_agent, response_parser_factory,
                 loader, exceptions_factory, loop=None):
        super().__init__(event_emitter, user_agent,
                         response_parser_factory, loader, exceptions_factory)
        self._loop = loop or asyncio.get_event_loop()

    # NOTE: we override this so we can pull out the custom AioConfig params and
    #       use an AioEndpointCreator
    def get_client_args(self, service_model, region_name, is_secure,
                        endpoint_url, verify, credentials, scoped_config,
                        client_config, endpoint_bridge):
        final_args = self.compute_client_args(
            service_model, client_config, endpoint_bridge, region_name,
            endpoint_url, is_secure, scoped_config)

        service_name = final_args['service_name']
        parameter_validation = final_args['parameter_validation']
        endpoint_config = final_args['endpoint_config']
        protocol = final_args['protocol']
        config_kwargs = final_args['config_kwargs']
        s3_config = final_args['s3_config']
        partition = endpoint_config['metadata'].get('partition', None)

        signing_region = endpoint_config['signing_region']
        endpoint_region_name = endpoint_config['region_name']
        if signing_region is None and endpoint_region_name is None:
            signing_region, endpoint_region_name = \
                self._get_default_s3_region(service_name, endpoint_bridge)
            config_kwargs['region_name'] = endpoint_region_name

        event_emitter = copy.copy(self._event_emitter)
        signer = RequestSigner(
            service_name, signing_region,
            endpoint_config['signing_name'],
            endpoint_config['signature_version'],
            credentials, event_emitter)

        config_kwargs['s3'] = s3_config

        if isinstance(client_config, AioConfig):
            connector_args = client_config.connector_args
        else:
            connector_args = None

        new_config = AioConfig(connector_args, **config_kwargs)
        endpoint_creator = AioEndpointCreator(event_emitter, self._loop)

        endpoint = endpoint_creator.create_endpoint(
            service_model, region_name=endpoint_region_name,
            endpoint_url=endpoint_config['endpoint_url'], verify=verify,
            response_parser_factory=self._response_parser_factory,
            max_pool_connections=new_config.max_pool_connections,
            proxies=new_config.proxies,
            timeout=(new_config.connect_timeout, new_config.read_timeout),
            connector_args=new_config.connector_args)

        serializer = botocore.serialize.create_serializer(
            protocol, parameter_validation)
        response_parser = botocore.parsers.create_parser(protocol)
        return {
            'serializer': serializer,
            'endpoint': endpoint,
            'response_parser': response_parser,
            'event_emitter': event_emitter,
            'request_signer': signer,
            'service_model': service_model,
            'loader': self._loader,
            'client_config': new_config,
            'partition': partition,
            'exceptions_factory': self._exceptions_factory,
            'loop': self._loop
        }
