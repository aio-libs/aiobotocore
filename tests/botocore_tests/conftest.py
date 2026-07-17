import pytest


@pytest.fixture
def anyio_backend():
    # Tests ported from botocore are not parametrized with the http backends
    # (see the pytest_generate_tests hook in tests/conftest.py). They build
    # their own clients, which use the asyncio-only aiohttp backend, so they
    # do not run on trio.
    return 'asyncio'
