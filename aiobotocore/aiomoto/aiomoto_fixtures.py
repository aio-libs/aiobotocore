"""
AWS asyncio test fixtures
"""

import aiobotocore.client
import aiobotocore.config
import pytest

from aiobotocore.aiomoto.aiomoto_services import MotoService
from aiobotocore.aiomoto.utils import AWS_ACCESS_KEY_ID
from aiobotocore.aiomoto.utils import AWS_SECRET_ACCESS_KEY


#
# Asyncio AWS Services
#


@pytest.fixture
async def aio_aws_batch_server():
    async with MotoService("batch") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_cloudformation_server():
    async with MotoService("cloudformation") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_ec2_server():
    async with MotoService("ec2") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_ecs_server():
    async with MotoService("ecs") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_iam_server():
    async with MotoService("iam") as svc:
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_dynamodb2_server():
    async with MotoService("dynamodb2") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_logs_server():
    # cloud watch logs
    async with MotoService("logs") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_s3_server():
    async with MotoService("s3") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_sns_server():
    async with MotoService("sns") as svc:
        svc.reset()
        yield svc.endpoint_url


@pytest.fixture
async def aio_aws_sqs_server():
    async with MotoService("sqs") as svc:
        svc.reset()
        yield svc.endpoint_url


#
# Asyncio AWS Clients
#


@pytest.fixture
def aio_aws_session(aws_credentials, aws_region, event_loop):
    # pytest-asyncio provides and manages the `event_loop`

    session = aiobotocore.get_session(loop=event_loop)
    session.user_agent_name = "aiomoto"

    assert session.get_default_client_config() is None
    aioconfig = aiobotocore.config.AioConfig(
        max_pool_connections=1, region_name=aws_region
    )

    # Note: tried to use proxies for the aiobotocore.endpoint, to replace
    #      'https://batch.us-west-2.amazonaws.com/v1/describejobqueues', but
    #      the moto.server does not behave as a proxy server.  Leaving this
    #      here for the record to avoid trying to do it again sometime later.
    # proxies = {
    #     'http': os.getenv("HTTP_PROXY", "http://127.0.0.1:5000/moto-api/"),
    #     'https': os.getenv("HTTPS_PROXY", "http://127.0.0.1:5000/moto-api/"),
    # }
    # assert aioconfig.proxies is None
    # aioconfig.proxies = proxies

    session.set_default_client_config(aioconfig)
    assert session.get_default_client_config() == aioconfig

    session.set_credentials(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    session.set_debug_logger(logger_name="aiomoto")

    yield session


@pytest.fixture
async def aio_aws_client(aio_aws_session):
    async def _get_client(service_name):
        async with MotoService(service_name) as srv:
            async with aio_aws_session.create_client(
                service_name, endpoint_url=srv.endpoint_url
            ) as client:
                yield client

    return _get_client


@pytest.fixture
async def aio_aws_batch_client(aio_aws_session, aio_aws_batch_server):
    async with aio_aws_session.create_client(
        "batch", endpoint_url=aio_aws_batch_server
    ) as client:
        yield client


@pytest.fixture
async def aio_aws_ec2_client(aio_aws_session, aio_aws_ec2_server):
    async with aio_aws_session.create_client(
        "ec2", endpoint_url=aio_aws_ec2_server
    ) as client:
        yield client


@pytest.fixture
async def aio_aws_ecs_client(aio_aws_session, aio_aws_ecs_server):
    async with aio_aws_session.create_client(
        "ecs", endpoint_url=aio_aws_ecs_server
    ) as client:
        yield client


@pytest.fixture
async def aio_aws_iam_client(aio_aws_session, aio_aws_iam_server):
    async with aio_aws_session.create_client(
        "iam", endpoint_url=aio_aws_iam_server
    ) as client:
        client.meta.config.region_name = "aws-global"  # not AWS_REGION
        yield client


@pytest.fixture
async def aio_aws_logs_client(aio_aws_session, aio_aws_logs_server):
    async with aio_aws_session.create_client(
        "logs", endpoint_url=aio_aws_logs_server
    ) as client:
        yield client


@pytest.fixture
async def aio_aws_s3_client(aio_aws_session, aio_aws_s3_server):
    async with aio_aws_session.create_client(
        "s3", endpoint_url=aio_aws_s3_server
    ) as client:
        yield client
