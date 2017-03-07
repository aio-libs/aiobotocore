from aiobotocore.config import AioConfig
from botocore.config import Config
from botocore.exceptions import ParamValidationError
import pytest


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_connector_args():
    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(use_dns_cache=1)
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(keepalive_timeout="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(force_close="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # wrong type
        connector_args = dict(ssl_context="1")
        AioConfig(connector_args)

    with pytest.raises(ParamValidationError):
        # invalid key
        connector_args = dict(foo="1")
        AioConfig(connector_args)

    # test merge
    cfg = Config(read_timeout=75)
    aio_cfg = AioConfig({'keepalive_timeout': 75})
    aio_cfg.merge(cfg)

    assert cfg.read_timeout == 75
    assert aio_cfg.connector_args['keepalive_timeout'] == 75
