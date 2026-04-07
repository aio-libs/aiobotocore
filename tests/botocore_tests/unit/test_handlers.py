# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

from unittest import mock

from aiobotocore import handlers


class TestHandlers:
    async def test_500_status_code_set_for_200_response(self):
        http_response = mock.Mock()
        http_response.status_code = 200

        async def content():
            return """
            <Error>
              <Code>AccessDenied</Code>
              <Message>Access Denied</Message>
              <RequestId>id</RequestId>
              <HostId>hostid</HostId>
            </Error>
        """

        http_response.content = content()
        await handlers.check_for_200_error((http_response, {}))
        assert http_response.status_code == 500

    async def test_200_response_with_no_error_left_untouched(self):
        http_response = mock.Mock()
        http_response.status_code = 200

        async def content():
            return "<NotAnError></NotAnError>"

        http_response.content = content()
        await handlers.check_for_200_error((http_response, {}))
        # We don't touch the status code since there are no errors present.
        assert http_response.status_code == 200

    async def test_500_response_can_be_none(self):
        # A 500 response can raise an exception, which means the response
        # object is None.  We need to handle this case.
        await handlers.check_for_200_error(None)
