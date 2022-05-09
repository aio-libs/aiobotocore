import datetime
from datetime import timezone
import pytest
from unittest import mock

import aiobotocore.credentials
import aiobotocore.session
import aiobotocore.signers


@pytest.mark.moto
@pytest.mark.asyncio
async def test_signers_generate_db_auth_token(rds_client):
    hostname = 'prod-instance.us-east-1.rds.amazonaws.com'
    port = 3306
    username = 'someusername'
    clock = datetime.datetime(2016, 11, 7, 17, 39, 33, tzinfo=timezone.utc)

    with mock.patch('datetime.datetime') as dt:
        dt.utcnow.return_value = clock
        result = await aiobotocore.signers.generate_db_auth_token(
            rds_client, hostname, port, username)

        result2 = await rds_client.generate_db_auth_token(
            hostname, port, username)

    # A scheme needs to be appended to the beginning or urlsplit may fail
    # on certain systems.
    assert result.startswith(
        'prod-instance.us-east-1.rds.amazonaws.com:3306/?AWSAccessKeyId=xxx&')
    assert result2.startswith(
        'prod-instance.us-east-1.rds.amazonaws.com:3306/?AWSAccessKeyId=xxx&')
