# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
import datetime
from datetime import timezone
from unittest import mock

import pytest
from botocore.exceptions import ParamValidationError

import aiobotocore.credentials
import aiobotocore.session
import aiobotocore.signers
from tests.boto_tests import assert_url_equal

DATE = datetime.datetime(2024, 11, 7, 17, 39, 33, tzinfo=timezone.utc)


async def test_signers_generate_db_auth_token(rds_client):
    hostname = 'prod-instance.us-east-1.rds.amazonaws.com'
    port = 3306
    username = 'someusername'
    clock = datetime.datetime(2016, 11, 7, 17, 39, 33, tzinfo=timezone.utc)

    with mock.patch('datetime.datetime') as dt:
        dt.utcnow.return_value = clock
        result = await aiobotocore.signers.generate_db_auth_token(
            rds_client, hostname, port, username
        )

        result2 = await rds_client.generate_db_auth_token(
            hostname, port, username
        )

    # A scheme needs to be appended to the beginning or urlsplit may fail
    # on certain systems.
    assert result.startswith(
        'prod-instance.us-east-1.rds.amazonaws.com:3306/?AWSAccessKeyId=xxx&'
    )
    assert result2.startswith(
        'prod-instance.us-east-1.rds.amazonaws.com:3306/?AWSAccessKeyId=xxx&'
    )


class TestDSQLGenerateDBAuthToken:
    @pytest.fixture(scope="session")
    def hostname(self):
        return 'test.dsql.us-east-1.on.aws'

    @pytest.fixture(scope="session")
    def action(self):
        return 'DbConnect'

    @pytest.fixture
    async def client(self, session):
        async with session.create_client(
            'dsql',
            region_name='us-east-1',
            aws_access_key_id='ACCESS_KEY',
            aws_secret_access_key='SECRET_KEY',
            aws_session_token="SESSION_TOKEN",
        ) as client:
            yield client

    async def test_dsql_generate_db_auth_token(
        self, client, hostname, action, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers._dsql_generate_db_auth_token(
            client, hostname, action
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnect'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '57fe03e060348aaa21405c239bf02572bbc911076e94dcd65c12ae569dd8fcf4'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_connect_auth_token(
        self, client, hostname, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers.dsql_generate_db_connect_auth_token(
            client, hostname
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnect'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '57fe03e060348aaa21405c239bf02572bbc911076e94dcd65c12ae569dd8fcf4'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_connect_admin_auth_token(
        self, client, hostname, time_machine
    ):
        time_machine.move_to(DATE, tick=False)

        result = await aiobotocore.signers.dsql_generate_db_connect_admin_auth_token(
            client, hostname
        )

        expected_result = (
            'test.dsql.us-east-1.on.aws/?Action=DbConnectAdmin'
            '&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential='
            'ACCESS_KEY%2F20241107%2Fus-east-1%2Fdsql%2Faws4_request'
            '&X-Amz-Date=20241107T173933Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host'
            '&X-Amz-Security-Token=SESSION_TOKEN&X-Amz-Signature='
            '5ac084bc7cabccc19a52a5d1b5c24b50d3ce143f43b659bd484c91aaf555e190'
        )

        # A scheme needs to be appended to the beginning or urlsplit may fail
        # on certain systems.
        assert_url_equal('https://' + result, 'https://' + expected_result)

    async def test_dsql_generate_db_auth_token_invalid_action(
        self, client, hostname
    ):
        with pytest.raises(ParamValidationError):
            await aiobotocore.signers._dsql_generate_db_auth_token(
                client, hostname, "FooBar"
            )
