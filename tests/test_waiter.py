import json
from inspect import iscoroutinefunction

import pytest

from aiobotocore.waiter import (
    AIOWaiter,
    WaiterModel,
    create_waiter_with_client,
)

from .conftest import random_name


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


async def test_create_waiter_with_custom_http_session_uses_asyncio(
    cloudformation_client, cloudformation_waiter_model, monkeypatch
):
    monkeypatch.setattr(
        cloudformation_client._endpoint, 'http_session', object()
    )
    waiter = create_waiter_with_client(
        'StackCreateComplete',
        cloudformation_waiter_model,
        cloudformation_client,
    )

    assert isinstance(waiter, AIOWaiter)


# CreateStack isn't idempotent: a retried request reports AlreadyExists.
@pytest.mark.config_kwargs(
    {'read_timeout': 60, 'retries': {'max_attempts': 0}}
)
async def test_sqs(cloudformation_client):
    # Random, not axis-derived: moto is global and axes get added (trio just did).
    stack_name = random_name()
    cloudformation_template = json.dumps(
        {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {
                "queue1": {
                    "Type": "AWS::SQS::Queue",
                    "Properties": {"QueueName": random_name()},
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
