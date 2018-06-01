import pytest
import hashlib
from dill.source import getsource
from yarl import URL

from aiobotocore.endpoint import ClientResponseProxy

from aiohttp.client import ClientResponse
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
_AIOHTTP_DIGESTS = {
    # for using _body
    ClientResponse: {'e178726065b609c69a1c02e8bb78f22efce90792'},
}

# These are guards to our main patches
_API_DIGESTS = {
    ClientArgsCreator: {'a8d2e4159469622afcf938805b17ca76aefec7e7'},
    ClientCreator: {'29a45581cadace0265620f925f76504be97f6fa0'},
    BaseClient: {'85e3d643193d71e6e7f43192cc1d8c9cf861fa59'},
    Config: {'c9261822caa7509d7b30b7738a9f034674061e35'},
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},
    Endpoint: {'5c5d568e23de1169916de6bcc58c72925f5a1adc'},
    EndpointCreator: {'00cb4303f8e9e775fe76996ad2f8852df7900398'},
    PageIterator: {'5a14db3ee7bc8773974b36cfdb714649b17a6a42'},
    Session: {'87b50bbf6caf0d7ae0ed1498032194ec36ca00f5'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    NormalizedOperationMethod: {'ee88834b123c6c77dfea0b4208308cd507a6ba36'},
}


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_patches():
    for obj, digests in _AIOHTTP_DIGESTS.items():
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
    resp.status_code = 500
    assert resp.status_code == 500
