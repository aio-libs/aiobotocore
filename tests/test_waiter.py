import pytest


@pytest.mark.moto
@pytest.mark.asyncio
async def test_sqs(cloudformation_client):
    cloudformation_template = """{
      "AWSTemplateFormatVersion": "2010-09-09",
      "Resources": {
        "queue1": {
          "Type": "AWS::SQS::Queue",
          "Properties": {
            "QueueName": "my-queue"
          }
        }
      }
    }"""

    # Create stack
    resp = await cloudformation_client.create_stack(
        StackName='my-stack', TemplateBody=cloudformation_template
    )

    assert resp['ResponseMetadata']['HTTPStatusCode'] == 200

    # wait for complete
    waiter = cloudformation_client.get_waiter('stack_create_complete')
    await waiter.wait(StackName='my-stack')
