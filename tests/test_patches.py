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
    TCPConnector: {'1e6a3c14ce703781253da5cfdc7847b4ae898234'},

    # for its inheritance to DataQueue
    ResponseHandler: {'5f11c28d0075e36dfec4705891f1c90248202ba4'},

    # for our replacement of read()
    DataQueue: {'8ad4d5df1d016547daea6389707bc656630582e5'},

    # for our patch of _wait
    StreamReader: {'c0a9a31a8c3e550de5985ab642028983f709b37b'},

    # for digging into _protocol ( 2.1.x, 2.2.x )
    ClientResponse: {'1dc0008e88b3f5ec2f59f6f5f03fae601f4a011d'},
}

# These are guards to our main patches
_API_DIGESTS = {
    ClientArgsCreator: {'a8d2e4159469622afcf938805b17ca76aefec7e7'},
    ClientCreator: {'7d862f8663e93c310b2fd7c0b0741ce45dc0eb47'},
    BaseClient: {'23756d283379717b1320315c98399ba284b2d17c'},
    Config: {'c9261822caa7509d7b30b7738a9f034674061e35'},
    convert_to_response_dict: {'ed634b3f0c24f8858aee8ed745051397270b1e46'},
    Endpoint: {'f1effcb1966ab690953f62a5cd48f510294a0440'},
    EndpointCreator: {'00cb4303f8e9e775fe76996ad2f8852df7900398'},
    PageIterator: {'5a14db3ee7bc8773974b36cfdb714649b17a6a42'},
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
