import pytest

from tests.botocore_tests import create_session


@pytest.fixture()
def patched_session(monkeypatch):
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'access_key')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'secret_key')
    monkeypatch.setenv('AWS_CONFIG_FILE', 'no-exist-foo')
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    monkeypatch.delenv('AWS_DEFAULT_REGION', raising=False)
    session = create_session()
    return session
