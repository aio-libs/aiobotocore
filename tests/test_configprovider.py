import pytest

from aiobotocore.config import AioConfig
from aiobotocore.session import AioSession


@pytest.mark.parametrize(
    'defaults_mode,retry_mode',
    [('legacy', 'legacy'), ('standard', 'standard'), ('auto', 'standard')],
)
async def test_defaults_mode(
    monkeypatch, defaults_mode, retry_mode, http_session_cls
):
    monkeypatch.setenv('AWS_DEFAULTS_MODE', defaults_mode)

    session = AioSession()

    assert session.get_config_variable('defaults_mode') == defaults_mode

    async with session.create_client(
        's3', config=AioConfig(http_session_cls=http_session_cls)
    ) as client:
        assert client.meta.config.retries['mode'] == retry_mode
