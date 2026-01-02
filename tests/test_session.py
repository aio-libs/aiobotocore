import logging

import pytest
from _pytest.logging import LogCaptureFixture

from aiobotocore import __version__, httpsession
from aiobotocore.config import AioConfig
from aiobotocore.session import AioSession


async def test_get_service_data(session):
    handler_called = False

    def handler(**kwargs):
        nonlocal handler_called
        handler_called = True

    session.register('service-data-loaded.s3', handler)
    await session.get_service_data('s3')

    assert handler_called


async def test_retry(
    session: AioSession, caplog: LogCaptureFixture, monkeypatch
):
    caplog.set_level(logging.DEBUG)

    config = AioConfig(
        connect_timeout=1,
        read_timeout=1,
        # this goes through a slightly different codepath than regular retries
        retries={
            "mode": "standard",
            "total_max_attempts": 3,
        },
    )

    async with session.create_client(
        's3',
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
        endpoint_url='http://localhost:7878',
    ) as client:
        # this needs the new style exceptions to work
        with pytest.raises(httpsession.EndpointConnectionError):
            await client.get_object(Bucket='foo', Key='bar')

        assert 'sleeping for' in caplog.text


async def test_set_user_agent_for_session(session: AioSession):
    assert session.user_agent_name == "aiobotocore"
    assert session.user_agent_version == __version__
    assert session.user_agent_extra.startswith("botocore/")


@pytest.mark.parametrize(
    "service_name, api_version",
    [
        ("s3", None),
        ("s3", "2006-03-01"),
        ("ec2", "2016-11-16"),
    ],
)
def test_warm_up_loader_caches(
    session: AioSession, service_name, api_version, mocker
):
    loader = mocker.Mock()
    get_component = mocker.patch.object(
        session, "get_component", return_value=loader
    )

    session.warm_up_loader_caches(service_name, api_version)

    get_component.assert_called_once_with("data_loader")
    assert loader.mock_calls == [
        mocker.call.load_data_with_path("endpoints"),
        mocker.call.load_data("sdk-default-configuration"),
        mocker.call.load_service_model(service_name, "waiters-2", api_version),
        mocker.call.load_service_model(
            service_name, "paginators-1", api_version
        ),
        mocker.call.load_service_model(
            service_name, type_name="service-2", api_version=api_version
        ),
        mocker.call.list_available_services(type_name="service-2"),
        mocker.call.load_data("partitions"),
        mocker.call.load_service_model(
            service_name, "service-2", api_version=api_version
        ),
        mocker.call.load_service_model(
            service_name, "endpoint-rule-set-1", api_version=api_version
        ),
        mocker.call.load_data("_retry"),
        mocker.call.load_service_model(
            service_name, "examples-1", api_version
        ),
    ]


@pytest.mark.parametrize(
    "warm_up_loader_caches",
    [False, True],
)
async def test_warm_up_loader_caches_config(
    session: AioSession,
    warm_up_loader_caches: bool,
    mocker,
):
    config = AioConfig(warm_up_loader_caches=warm_up_loader_caches)
    mocker.patch.object(
        session, "warm_up_loader_caches", wraps=session.warm_up_loader_caches
    )

    async with session.create_client(
        "s3",
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    ):
        pass

    if warm_up_loader_caches:
        session.warm_up_loader_caches.assert_called_once_with("s3", None)
    else:
        session.warm_up_loader_caches.assert_not_called()


@pytest.mark.parametrize(
    "warm_up_loader_caches",
    [False, True],
)
async def test_non_blocking_create_client(
    session: AioSession,
    warm_up_loader_caches: bool,
    mocker,
):
    config = AioConfig(warm_up_loader_caches=warm_up_loader_caches)
    loader = session.get_component("data_loader")
    file_loader = mocker.patch.object(
        loader, "file_loader", wraps=loader.file_loader
    )
    # perform implicit warm-up, while avoiding any other file I/O by stubbing relevant codepathes
    session._internal_components.lazy_register_component(
        'endpoint_resolver', lambda: None
    )
    mocker.patch.object(
        session, "_resolve_defaults_mode", return_value="legacy"
    )
    client_creator_cls_mock = mocker.patch(
        "aiobotocore.session.AioClientCreator", autospec=True
    )

    async with session.create_client(
        "s3",
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    ):
        pass

    if warm_up_loader_caches:
        # warm-up triggered file I/O (non-blocking)
        file_loader.exists.assert_called()
        file_loader.load_file.assert_called()
    else:
        # no file I/O
        file_loader.exists.assert_not_called()
        file_loader.load_file.assert_not_called()

    mocker.stop(client_creator_cls_mock)
    session._register_endpoint_resolver()
    file_loader.reset_mock()

    # regular client creation #1
    async with session.create_client(
        "s3",
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    ):
        pass

    if warm_up_loader_caches:
        # no file I/O
        file_loader.exists.assert_not_called()
        file_loader.load_file.assert_not_called()
    else:
        # file I/O (blocking)
        file_loader.exists.assert_called()
        file_loader.load_file.assert_called()

    file_loader.reset_mock()

    # regular client creation #2
    async with session.create_client(
        "s3",
        config=config,
        aws_secret_access_key="xxx",
        aws_access_key_id="xxx",
    ):
        pass

    # no file I/O
    file_loader.exists.assert_not_called()
    file_loader.load_file.assert_not_called()
