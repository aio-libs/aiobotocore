import json
import pytest
import botocore

from moto.sns.models import DEFAULT_TOPIC_POLICY


@pytest.mark.moto
@pytest.mark.asyncio
async def test_topic_attributes(sns_client, topic_arn):
    response = await sns_client.list_topics()
    pytest.aio.assert_status_code(response, 200)
    arn1 = response['Topics'][0]['TopicArn']
    topic_properties = await sns_client.get_topic_attributes(
        TopicArn=arn1
    )
    attributes = topic_properties['Attributes']

    assert arn1 == topic_arn
    assert json.loads(attributes['Policy']) == DEFAULT_TOPIC_POLICY
    assert attributes['DisplayName'] == ''

    display_name = 'My display name'
    await sns_client.set_topic_attributes(TopicArn=arn1,
                                          AttributeName='DisplayName',
                                          AttributeValue=display_name)

    topic_properties = await sns_client.get_topic_attributes(
        TopicArn=arn1
    )
    attributes = topic_properties['Attributes']
    assert attributes['DisplayName'] == display_name


@pytest.mark.moto
@pytest.mark.asyncio
async def test_creating_subscription(sns_client, topic_arn):
    response = await sns_client.subscribe(TopicArn=topic_arn,
                                          Protocol="http",
                                          Endpoint="http://httpbin.org/")
    subscription_arn = response['SubscriptionArn']
    subscriptions = (
        await sns_client.list_subscriptions()
    )["Subscriptions"]
    assert len([s for s in subscriptions if s['Protocol'] == 'http']) == 1

    await sns_client.unsubscribe(SubscriptionArn=subscription_arn)
    subscriptions = (
        await sns_client.list_subscriptions()
    )["Subscriptions"]
    assert len([s for s in subscriptions if s['Protocol'] == 'http']) == 0


@pytest.mark.moto
@pytest.mark.asyncio
async def test_publish_to_http(sns_client, topic_arn):
    response = await sns_client.subscribe(
        TopicArn=topic_arn,
        Protocol='http',
        Endpoint="http://httpbin.org/endpoint"
    )
    subscription_arn = response['SubscriptionArn']

    response = await sns_client.publish(
        TopicArn=topic_arn,
        Message="Test msg",
        Subject="my subject",
    )
    pytest.aio.assert_status_code(response, 200)
    await sns_client.unsubscribe(SubscriptionArn=subscription_arn)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_missing_endpoint_attributes(sns_client):
    with pytest.raises(botocore.exceptions.ClientError):
        await sns_client.get_endpoint_attributes(EndpointArn="arn1")


@pytest.mark.moto
@pytest.mark.asyncio
async def test_platform_applications(sns_client):
    await sns_client.create_platform_application(
        Name="app1",
        Platform="APNS",
        Attributes={},
    )
    await sns_client.create_platform_application(
        Name="app2",
        Platform="APNS",
        Attributes={},
    )

    repsonse = await sns_client.list_platform_applications()
    apps = repsonse['PlatformApplications']
    assert len(apps) == 2
