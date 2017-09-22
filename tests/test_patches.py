import pytest
import hashlib
from dill.source import getsource
from yarl import URL

from aiobotocore.endpoint import ClientResponseProxy

from aiohttp.client_proto import ResponseHandler
from aiohttp import TCPConnector
from aiohttp.client import ClientResponse
from aiohttp.streams import DataQueue, StreamReader
from botocore.args import ClientArgsCreator
from botocore.client import ClientCreator, BaseClient, Config
from botocore.endpoint import convert_to_response_dict, Endpoint, \
    EndpointCreator
from botocore.paginate import PageIterator
from botocore.session import Session, get_session


# This file ensures that our private patches will work going forward.  If a
# method gets updated this will assert and someone will need to validate:
# 1) If our code needs to be updated
# 2) If our minimum botocore version needs to be updated
# 3) If we need to replace the below hash or add to the set

# The follow is for our monkeypatches for read_timeout:
#    github.com/aio-libs/aiobotocore/pull/248
_READ_TIMEOUT_DIGESTS = {
    # for our replacement of _factory
    TCPConnector: {'a153acda1bfc891d01a56597f77b33afbd786d3d'},

    # for its inheritance to DataQueue
    ResponseHandler: {'1cfdb033cb4b4d586bbebf49bed3c2930c026809'},

    # for our replacement of read()
    DataQueue: {'8ad4d5df1d016547daea6389707bc656630582e5'},

    # for our patch of _wait
    StreamReader: {'dc5a5a33e86cedf2d2c8413d951d2274a79303f4'},

    # for digging into _protocol ( 2.1.x, 2.2.x )
    ClientResponse: {'d1e0c16dea4fe3426caa1e9b0dc9f5f1992d838e',
                     'bc374038ac3bfd7cc13dadb6aebbf0f67ebb7620'},
}

# These are guards to our main patches
_API_DIGESTS = {
    ClientArgsCreator: {'a8d2e4159469622afcf938805b17ca76aefec7e7'},
    ClientCreator: {'7d862f8663e93c310b2fd7c0b0741ce45dc0eb47'},
    BaseClient: {'3e87626900a1374583cd9ecaecd6d58153a50cb6'},
    Config: {'c9261822caa7509d7b30b7738a9f034674061e35'},
    convert_to_response_dict: {'ed634b3f0c24f8858aee8ed745051397270b1e46'},
    Endpoint: {'7aa956cf3f28f573384dbaeb2f819b0a05724e65'},
    EndpointCreator: {'00cb4303f8e9e775fe76996ad2f8852df7900398'},
    PageIterator: {'d6d83b5c9314d4346ce021c85986b7c090569a34'},
    Session: {'87b50bbf6caf0d7ae0ed1498032194ec36ca00f5'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
}


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_patches():
    for obj, digests in _READ_TIMEOUT_DIGESTS.items():
        digest = hashlib.sha1(getsource(obj).encode('utf-8')).hexdigest()
        assert digest in digests, \
            "Digest of {} not found in: {}".format(obj.__name__, digests)

    for obj, digests in _API_DIGESTS.items():
        digest = hashlib.sha1(getsource(obj).encode('utf-8')).hexdigest()
        assert digest in digests, \
            "Digest of {} not found in: {}".format(obj.__name__, digests)


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_set_status_code():
    resp = ClientResponseProxy('GET', URL('http://foo/bar'))
    resp.status_code = 500
    assert resp.status_code == 500
