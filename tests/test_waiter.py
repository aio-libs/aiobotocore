import json
from inspect import iscoroutinefunction

import pytest

from aiobotocore.waiter import (
    AIOWaiter,
    WaiterModel,
    create_waiter_with_client,
)


@pytest.fixture
def cloudformation_waiter_model(cloudformation_client):
    config = cloudformation_client._get_waiter_config()
    return WaiterModel(config)


async def test_create_waiter_with_client(
    cloudformation_client, cloudformation_waiter_model
):
    waiter = create_waiter_with_client(
        'StackCreateComplete',
        cloudformation_waiter_model,
        cloudformation_client,
    )
    assert isinstance(waiter, AIOWaiter)
    assert iscoroutinefunction(waiter.wait)


async def test_create_waiter_with_unknown_http_session(
    cloudformation_client, cloudformation_waiter_model, monkeypatch
):
    monkeypatch.setattr(
        cloudformation_client._endpoint, 'http_session', object()
    )
    with pytest.raises(TypeError, match='unknown http session type'):
        create_waiter_with_client(
            'StackCreateComplete',
            cloudformation_waiter_model,
            cloudformation_client,
        )


async def test_sqs(
    cloudformation_client,
    request,
    anyio_backend: str,
    current_http_backend: str,
):
    # The http backend alone is shared by the asyncio and trio httpx ids, which
    # xdist can run concurrently against the one moto server, so the async
    # backend has to be part of the name too — for the queue as well as the
    # stack, since two stacks cannot both create my-queue. The attempt number
    # keeps a rerun off the stack its failed attempt left behind;
    # pytest-rerunfailures only sets execution_count when reruns are enabled.
    attempt = getattr(request.node, 'execution_count', 1)
    unique = f'{anyio_backend}-{current_http_backend}-{attempt}'
    stack_name = f'my-stack-{unique}'
    cloudformation_template = json.dumps(
        {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {
                "queue1": {
                    "Type": "AWS::SQS::Queue",
                    "Properties": {"QueueName": f"my-queue-{unique}"},
                }
            },
        }
    )

    # Create stack
    resp = await cloudformation_client.create_stack(
        StackName=stack_name, TemplateBody=cloudformation_template
    )

    assert resp['ResponseMetadata']['HTTPStatusCode'] == 200

    # wait for complete
    waiter = cloudformation_client.get_waiter('stack_create_complete')
    await waiter.wait(StackName=stack_name)

    await cloudformation_client.delete_stack(StackName=stack_name)
