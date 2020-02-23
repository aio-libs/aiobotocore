import pytest

from aiobotocore.aiomoto.utils import response_success


@pytest.fixture
def aio_s3_bucket_name() -> str:
    return "aio_moto_bucket"


@pytest.fixture
async def aio_s3_bucket(aio_s3_bucket_name, aio_aws_s3_client) -> str:
    resp = await aio_aws_s3_client.create_bucket(Bucket=aio_s3_bucket_name)
    assert response_success(resp)
    head = await aio_aws_s3_client.head_bucket(Bucket=aio_s3_bucket_name)
    assert response_success(head)
    return aio_s3_bucket_name


@pytest.mark.asyncio
async def test_aio_aws_bucket_access(aio_aws_s3_client, aio_s3_bucket):
    resp = await aio_aws_s3_client.list_buckets()
    assert response_success(resp)
    bucket_names = [b["Name"] for b in resp["Buckets"]]
    assert bucket_names == [aio_s3_bucket]
