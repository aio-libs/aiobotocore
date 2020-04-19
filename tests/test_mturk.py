import pytest
import aiobotocore.session


# @pytest.mark.moto  unfortunately moto doesn't support this yet :(
@pytest.mark.asyncio
async def test_mturk():
    session = aiobotocore.session.AioSession(profile='thehesiod')
    async with session.create_client('mturk') as client:
        await client.list_hi_ts_for_qualification_type(QualificationTypeId='string')
