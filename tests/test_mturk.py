import pytest
from botocore.stub import ANY, Stubber

_mturk_list_hits_response = {
    'NumResults': 0,
    'HITs': [],
    'ResponseMetadata': {
        'RequestId': '00000000-4989-4ffc-85cd-aaaaaaaaaaaa',
        'HTTPStatusCode': 200,
        'HTTPHeaders': {
            'x-amzn-requestid': '00000000-4989-4ffc-85cd-aaaaaaaaaaaa',
            'content-type': 'application/x-amz-json-1.1',
            'content-length': '26',
            'date': 'Thu, 04 Jun 2020 00:48:16 GMT',
        },
        'RetryAttempts': 0,
    },
}


# Unfortunately moto does not support mturk yet
# Also looks like we won't be able to support this (see notes from 1.0.6 release)
# @pytest.mark.moto
@pytest.mark.asyncio
async def test_mturk_stubber(session):
    async with session.create_client(
        'mturk', region_name='us-east-1'
    ) as client:
        with Stubber(client) as stubber:
            stubber.add_response(
                'list_hits_for_qualification_type',
                _mturk_list_hits_response,
                {'QualificationTypeId': ANY},
            )

            response = await client.list_hits_for_qualification_type(
                QualificationTypeId='string'
            )
            assert response == _mturk_list_hits_response
