import asyncio
import sys

import botocore.client

from botocore.exceptions import OperationNotPageableError
from botocore.paginate import Paginator
from botocore.utils import get_service_module_name
from botocore.waiter import xform_name

from .paginate import AioPageIterator
from .args import AioClientArgsCreator
from . import waiter
PY_35 = sys.version_info >= (3, 5)


class AioClientCreator(botocore.client.ClientCreator):

    def __init__(self, loader, endpoint_resolver, user_agent, event_emitter,
                 retry_handler_factory, retry_config_translator,
                 response_parser_factory=None, exceptions_factory=None,
                 loop=None):
        super().__init__(loader, endpoint_resolver, user_agent, event_emitter,
                         retry_handler_factory, retry_config_translator,
                         response_parser_factory=response_parser_factory,
                         exceptions_factory=exceptions_factory)
        loop = loop or asyncio.get_event_loop()
        self._loop = loop

    def _get_client_args(self, service_model, region_name, is_secure,
                         endpoint_url, verify, credentials,
                         scoped_config, client_config, endpoint_bridge):
        # This is a near copy of botocore.client.ClientCreator. What's replaced
        # is ClientArgsCreator->AioClientArgsCreator
        args_creator = AioClientArgsCreator(
            self._event_emitter, self._user_agent,
            self._response_parser_factory, self._loader,
            self._exceptions_factory, loop=self._loop)
        return args_creator.get_client_args(
            service_model, region_name, is_secure, endpoint_url,
            verify, credentials, scoped_config, client_config, endpoint_bridge)

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
        operation_model = self._service_model.operation_model(operation_name)
        request_context = {
            'client_region': self.meta.region_name,
            'client_config': self.meta.config,
            'has_streaming_input': operation_model.has_streaming_input,
            'auth_type': operation_model.auth_type,
        }
        request_dict = self._convert_to_request_dict(
            api_params, operation_model, context=request_context)

        handler, event_response = self.meta.events.emit_until_response(
            'before-call.{endpoint_prefix}.{operation_name}'.format(
                endpoint_prefix=self._service_model.endpoint_prefix,
                operation_name=operation_name),
            model=operation_model, params=request_dict,
            request_signer=self._request_signer, context=request_context)

        if event_response is not None:
            http, parsed_response = event_response
        else:
            http, parsed_response = yield from self._endpoint.make_request(
                operation_model, request_dict)

        self.meta.events.emit(
            'after-call.{endpoint_prefix}.{operation_name}'.format(
                endpoint_prefix=self._service_model.endpoint_prefix,
                operation_name=operation_name),
            http_response=http, parsed=parsed_response,
            model=operation_model, context=request_context
        )

        if http.status_code >= 300:
            error_code = parsed_response.get("Error", {}).get("Code")
            error_class = self.exceptions.from_code(error_code)
            raise error_class(parsed_response, operation_name)
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

    def get_waiter(self, waiter_name):
        config = self._get_waiter_config()
        if not config:
            raise ValueError("Waiter does not exist: %s" % waiter_name)
        model = waiter.WaiterModel(config)
        mapping = {}
        for name in model.waiter_names:
            mapping[xform_name(name)] = name
        if waiter_name not in mapping:
            raise ValueError("Waiter does not exist: %s" % waiter_name)

        return waiter.create_waiter_with_client(
            mapping[waiter_name], model, self)

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
        """Close all http connections. This is coroutine, and should be
        awaited. Method will be coroutine (instead returning Future) once
        aiohttp does that.
        """
        return self._endpoint._aio_session.close()
