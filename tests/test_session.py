import pytest


@pytest.mark.moto
@pytest.mark.asyncio
async def test_get_service_data(session):
    handler_called = False

    def handler(**kwargs):
        nonlocal handler_called
        handler_called = True

    session.register('service-data-loaded.s3', handler)
    await session.get_service_data('s3')

    assert handler_called
