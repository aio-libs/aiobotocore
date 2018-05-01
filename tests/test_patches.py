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
from botocore.waiter import NormalizedOperationMethod


# This file ensures that our private patches will work going forward.  If a
# method gets updated this will assert and someone will need to validate:
# 1) If our code needs to be updated
# 2) If our minimum botocore version needs to be updated
# 3) If we need to replace the below hash (not backwards compatible) or add
#    to the set

# The follow is for our monkeypatches for read_timeout:
#    github.com/aio-libs/aiobotocore/pull/248
_READ_TIMEOUT_DIGESTS = {
    # for our replacement of _factory and _create_connection
    TCPConnector: {
        '42a405b3d0b4aa9a61eb7d72925a5c8e373bec6b',
        '3d92dd47383d5a6f918b56a946a975314c8359f1'
    },

    # for its inheritance to DataQueue
    ResponseHandler: {'96d9eb3f04ff80a2acaf2fc18a103db474a3c965'},

    # for our replacement of read()
    DataQueue: {'be516f7bcdbf5235218261d8ed1f490d299f611d'},

    # for our patch of _wait
    StreamReader: {'d4ffb6ae823ef4bfd810aade8601ba7b01aa08ec'},

    # for digging into _protocol, and using _body
    ClientResponse: {'5834c5174937df460b2452b68942e950bbaa5dd7'},
}

# These are guards to our main patches
_API_DIGESTS = {
    ClientArgsCreator: {'a8d2e4159469622afcf938805b17ca76aefec7e7'},
    ClientCreator: {'9eef0e5fbd62fc495a5eee2dab118e36ae496dce'},
    BaseClient: {'23756d283379717b1320315c98399ba284b2d17c'},
    Config: {'c9261822caa7509d7b30b7738a9f034674061e35'},
    convert_to_response_dict: {'ed634b3f0c24f8858aee8ed745051397270b1e46'},
    Endpoint: {'f1effcb1966ab690953f62a5cd48f510294a0440'},
    EndpointCreator: {'00cb4303f8e9e775fe76996ad2f8852df7900398'},
    PageIterator: {'5a14db3ee7bc8773974b36cfdb714649b17a6a42'},
    Session: {'87b50bbf6caf0d7ae0ed1498032194ec36ca00f5'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    NormalizedOperationMethod: {'ee88834b123c6c77dfea0b4208308cd507a6ba36'},
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
def test_set_status_code(event_loop):
    resp = ClientResponseProxy(
        'GET', URL('http://foo/bar'),
        writer=None, continue100=None, timer=None,
        request_info=None,
        auto_decompress=None,
        traces=None,
        loop=event_loop,
        session=None)
    assert resp.status_code == 500
