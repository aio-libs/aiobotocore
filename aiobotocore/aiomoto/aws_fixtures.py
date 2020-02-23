"""
AWS test fixtures
"""
import os

import boto3
import pytest

from moto import mock_batch
from moto import mock_ec2
from moto import mock_ecs
from moto import mock_iam
from moto import mock_logs
from moto import mock_s3

from aiobotocore.aiomoto.utils import AWS_REGION
from aiobotocore.aiomoto.utils import AWS_ACCESS_KEY_ID
from aiobotocore.aiomoto.utils import AWS_SECRET_ACCESS_KEY

AWS_HOST = "127.0.0.1"
AWS_PORT = "5000"


@pytest.fixture
def aws_host():
    return os.getenv("AWS_HOST", AWS_HOST)


@pytest.fixture
def aws_port():
    return os.getenv("AWS_PORT", AWS_PORT)


@pytest.fixture
def aws_proxy(aws_host, aws_port, monkeypatch):
    # only required if using a moto stand-alone server or similar local stack
    monkeypatch.setenv("HTTP_PROXY", f"http://{aws_host}:{aws_port}")
    monkeypatch.setenv("HTTPS_PROXY", f"http://{aws_host}:{aws_port}")


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "test")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test")


@pytest.fixture
def aws_region():
    return AWS_REGION


#
# AWS Clients
#


@pytest.fixture
def aws_batch_client(aws_region):
    with mock_batch():
        yield boto3.client("batch", region_name=aws_region)


@pytest.fixture
def aws_ec2_client(aws_region):
    with mock_ec2():
        yield boto3.client("ec2", region_name=aws_region)


@pytest.fixture
def aws_ecs_client(aws_region):
    with mock_ecs():
        yield boto3.client("ecs", region_name=aws_region)


@pytest.fixture
def aws_iam_client(aws_region):
    with mock_iam():
        yield boto3.client("iam", region_name=aws_region)


@pytest.fixture
def aws_logs_client(aws_region):
    with mock_logs():
        yield boto3.client("logs", region_name=aws_region)


@pytest.fixture
def aws_s3_client(aws_region):
    with mock_s3():
        yield boto3.client("s3", region_name=aws_region)
