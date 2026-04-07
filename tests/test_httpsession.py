import botocore
import pytest


async def test_cannot_create_client_sessions_outside_context(session):
    s3_client_context = session.create_client(
        's3',
        'us-west-2',
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    )

    async with s3_client_context as s3_client:
        pass

    with pytest.raises(
        botocore.exceptions.HTTPClientError,
        match="'NoneType' object has no attribute 'get'",
    ):
        await s3_client.list_buckets()
