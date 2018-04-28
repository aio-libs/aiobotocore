import asyncio

# WaiterModel is required for client.py import
from botocore.exceptions import ClientError
from botocore.waiter import WaiterModel  # noqa: F401
from botocore.waiter import Waiter, xform_name, logger, WaiterError
from botocore.docs.docstring import WaiterDocstring
from botocore.utils import get_service_module_name


class NormalizedOperationMethod:
    def __init__(self, client_method):
        self._client_method = client_method

    async def __call__(self, **kwargs):
        try:
            return await self._client_method(**kwargs)
        except ClientError as e:
            return e.response


class AIOWaiter(Waiter):
    def __init__(self, *args, **kwargs):
        self._loop = kwargs.pop('loop', None) or asyncio.get_event_loop()
        super().__init__(*args, **kwargs)

    async def wait(self, **kwargs):
        acceptors = list(self.config.acceptors)
        current_state = 'waiting'
        # pop the invocation specific config
        config = kwargs.pop('WaiterConfig', {})
        sleep_amount = config.get('Delay', self.config.delay)
        max_attempts = config.get('MaxAttempts', self.config.max_attempts)
        num_attempts = 0

        while True:
            response = await self._operation_method(**kwargs)
            num_attempts += 1
            for acceptor in acceptors:
                if acceptor.matcher_func(response):
                    current_state = acceptor.state
                    break
            else:
                # If none of the acceptors matched, we should
                # transition to the failure state if an error
                # response was received.
                if 'Error' in response:
                    # Transition to a failure state, which we
                    # can just handle here by raising an exception.
                    raise WaiterError(
                        name=self.name,
                        reason=response['Error'].get('Message', 'Unknown'),
                        last_response=response
                    )
            if current_state == 'success':
                logger.debug("Waiting complete, waiter matched the "
                             "success state.")
                return
            if current_state == 'failure':
                raise WaiterError(
                    name=self.name,
                    reason='Waiter encountered a terminal failure state',
                    last_response=response,
                )
            if num_attempts >= max_attempts:
                raise WaiterError(
                    name=self.name,
                    reason='Max attempts exceeded',
                    last_response=response
                )
            await asyncio.sleep(sleep_amount, loop=self._loop)


def create_waiter_with_client(waiter_name, waiter_model, client, loop):
    """

    :type waiter_name: str
    :param waiter_name: The name of the waiter.  The name should match
        the name (including the casing) of the key name in the waiter
        model file (typically this is CamelCasing).

    :type waiter_model: botocore.waiter.WaiterModel
    :param waiter_model: The model for the waiter configuration.

    :type client: botocore.client.BaseClient
    :param client: The botocore client associated with the service.

    :rtype: botocore.waiter.Waiter
    :return: The waiter object.

    """
    single_waiter_config = waiter_model.get_waiter(waiter_name)
    operation_name = xform_name(single_waiter_config.operation)
    operation_method = NormalizedOperationMethod(
        getattr(client, operation_name))

    # Create a new wait method that will serve as a proxy to the underlying
    # Waiter.wait method. This is needed to attach a docstring to the
    # method.
    async def wait(self, **kwargs):
        await AIOWaiter.wait(self, **kwargs)

    wait.__doc__ = WaiterDocstring(
        waiter_name=waiter_name,
        event_emitter=client.meta.events,
        service_model=client.meta.service_model,
        service_waiter_model=waiter_model,
        include_signature=False
    )

    # Rename the waiter class based on the type of waiter.
    waiter_class_name = str('%s.AIOWaiter.%s' % (
        get_service_module_name(client.meta.service_model),
        waiter_name))

    # Create the new waiter class
    documented_waiter_cls = type(
        waiter_class_name, (AIOWaiter,), {'wait': wait})

    # Return an instance of the new waiter class.
    return documented_waiter_cls(
        waiter_name, single_waiter_config, operation_method, loop=loop
    )
