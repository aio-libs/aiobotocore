import asyncio
import copy
import sys

import botocore.auth
import botocore.client
import botocore.parsers
import botocore.serialize
import botocore.validate

from botocore.client import ClientEndpointBridge
from botocore.exceptions import ClientError, OperationNotPageableError
from botocore.paginate import Paginator
from botocore.signers import RequestSigner
from botocore.utils import get_service_module_name

from .config import AioConfig
from .endpoint import AioEndpointCreator
from .paginate import AioPageIterator

PY_35 = sys.version_info >= (3, 5)


class AioClientCreator(botocore.client.ClientCreator):

    def __init__(self, loader, endpoint_resolver, user_agent, event_emitter,
                 retry_handler_factory, retry_config_translator,
                 response_parser_factory=None, loop=None):
        super().__init__(loader, endpoint_resolver, user_agent, event_emitter,
                         retry_handler_factory, retry_config_translator,
                         response_parser_factory=response_parser_factory)
        loop = loop or asyncio.get_event_loop()
        self._loop = loop

    def _get_client_args(self, service_model, region_name, is_secure,
                         endpoint_url, verify, credentials,
                         scoped_config, client_config):

        # This is a near copy of botocore.client.ClientCreator. What's replaced
        # is Config->AioConfig and EndpointCreator->AioEndpointCreator
        # We don't re-use the parent's implementations due to weak refs

        service_name = service_model.endpoint_prefix
        protocol = service_model.metadata['protocol']
        parameter_validation = True
        if client_config:
            parameter_validation = client_config.parameter_validation
        serializer = botocore.serialize.create_serializer(
            protocol, parameter_validation)

        event_emitter = copy.copy(self._event_emitter)
        response_parser = botocore.parsers.create_parser(protocol)
        endpoint_bridge = ClientEndpointBridge(
            self._endpoint_resolver, scoped_config, client_config,
            service_signing_name=service_model.metadata.get('signingName'))
        endpoint_config = endpoint_bridge.resolve(
            service_name, region_name, endpoint_url, is_secure)

        # Override the user agent if specified in the client config.
        user_agent = self._user_agent
        if client_config is not None:
            if client_config.user_agent is not None:
                user_agent = client_config.user_agent
            if client_config.user_agent_extra is not None:
                user_agent += ' %s' % client_config.user_agent_extra

        signer = RequestSigner(
            service_name, endpoint_config['signing_region'],
            endpoint_config['signing_name'],
            endpoint_config['signature_version'],
            credentials, event_emitter)

        # Create a new client config to be passed to the client based
        # on the final values. We do not want the user to be able
        # to try to modify an existing client with a client config.
        config_kwargs = dict(
            region_name=endpoint_config['region_name'],
            signature_version=endpoint_config['signature_version'],
            user_agent=user_agent)
        if client_config is not None:
            config_kwargs.update(
                connect_timeout=client_config.connect_timeout,
                read_timeout=client_config.read_timeout)

        # Add any additional s3 configuration for client
        self._inject_s3_configuration(
            config_kwargs, scoped_config, client_config)

        if isinstance(client_config, AioConfig):
            connector_args = client_config.connector_args
        else:
            connector_args = None

        new_config = AioConfig(connector_args, **config_kwargs)
        endpoint_creator = AioEndpointCreator(event_emitter, self._loop)
        endpoint = endpoint_creator.create_endpoint(
            service_model, region_name=endpoint_config['region_name'],
            endpoint_url=endpoint_config['endpoint_url'], verify=verify,
            response_parser_factory=self._response_parser_factory,
            timeout=(new_config.connect_timeout, new_config.read_timeout),
            connector_args=new_config.connector_args)

        return {
            'serializer': serializer,
            'endpoint': endpoint,
            'response_parser': response_parser,
            'event_emitter': event_emitter,
            'request_signer': signer,
            'service_model': service_model,
            'loader': self._loader,
            'client_config': new_config
        }

    def _create_client_class(self, service_name, service_model):
        class_attributes = self._create_methods(service_model)
        py_name_to_operation_name = self._create_name_mapping(service_model)
        class_attributes['_PY_TO_OP_NAME'] = py_name_to_operation_name
        bases = [AioBaseClient]
        self._event_emitter.emit('creating-client-class.%s' % service_name,
                                 class_attributes=class_attributes,
                                 base_classes=bases)
        class_name = get_service_module_name(service_model)
        cls = type(str(class_name), tuple(bases), class_attributes)
        return cls


class AioBaseClient(botocore.client.BaseClient):
    @asyncio.coroutine
    def _make_api_call(self, operation_name, api_params):
        request_context = {}
        operation_model = self._service_model.operation_model(operation_name)
        request_dict = self._convert_to_request_dict(
            api_params, operation_model, context=request_context)

        self.meta.events.emit(
            'before-call.{endpoint_prefix}.{operation_name}'.format(
                endpoint_prefix=self._service_model.endpoint_prefix,
                operation_name=operation_name),
            model=operation_model, params=request_dict,
            request_signer=self._request_signer, context=request_context
        )

        http, parsed_response = yield from self._endpoint.make_request(
            operation_model, request_dict)

        self.meta.events.emit(
            'after-call.{endpoint_prefix}.{operation_name}'.format(
                endpoint_prefix=self._service_model.endpoint_prefix,
                operation_name=operation_name),
            http_response=http, parsed=parsed_response,
            model=operation_model, context=request_context
        )

        if http.status >= 300:
            raise ClientError(parsed_response, operation_name)
        else:
            return parsed_response

    def get_paginator(self, operation_name):
        """Create a paginator for an operation.

        :type operation_name: string
        :param operation_name: The operation name.  This is the same name
            as the method name on the client.  For example, if the
            method name is ``create_foo``, and you'd normally invoke the
            operation as ``client.create_foo(**kwargs)``, if the
            ``create_foo`` operation can be paginated, you can use the
            call ``client.get_paginator("create_foo")``.

        :raise OperationNotPageableError: Raised if the operation is not
            pageable.  You can use the ``client.can_paginate`` method to
            check if an operation is pageable.

        :rtype: L{botocore.paginate.Paginator}
        :return: A paginator object.

        """
        if not self.can_paginate(operation_name):
            raise OperationNotPageableError(operation_name=operation_name)
        else:
            actual_operation_name = self._PY_TO_OP_NAME[operation_name]
            # substitute iterator with async one
            Paginator.PAGE_ITERATOR_CLS = AioPageIterator
            paginator = Paginator(
                getattr(self, operation_name),
                self._cache['page_config'][actual_operation_name])
            return paginator

    if PY_35:
        @asyncio.coroutine
        def __aenter__(self):
            yield from self._endpoint._aio_session.__aenter__()
            return self

        @asyncio.coroutine
        def __aexit__(self, exc_type, exc_val, exc_tb):
            yield from self._endpoint._aio_session.__aexit__(exc_type,
                                                             exc_val, exc_tb)

    def close(self):
        """Close all http connections"""
        # ClientSession.close() from aiohttp returns asyncio.Future here so
        # this method could be used with yield from/await
        return self._endpoint._aio_session.close()
