import pytest

from aiobotocore.aiomoto.utils import response_success


@pytest.fixture
def s3_bucket_name() -> str:
    return "moto_bucket"


@pytest.fixture
def s3_bucket(s3_bucket_name, aws_s3_client) -> str:
    resp = aws_s3_client.create_bucket(Bucket=s3_bucket_name)
    assert response_success(resp)
    head = aws_s3_client.head_bucket(Bucket=s3_bucket_name)
    assert response_success(head)
    return s3_bucket_name


def test_aws_bucket_access(aws_s3_client, s3_bucket):
    resp = aws_s3_client.list_buckets()
    assert response_success(resp)
    bucket_names = [b["Name"] for b in resp["Buckets"]]
    assert bucket_names == [s3_bucket]
