import pytest
import hashlib
from dill.source import getsource
from yarl import URL

from aiobotocore.endpoint import ClientResponseProxy

import aiohttp
from aiohttp.client import ClientResponse
import botocore
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

# !!! README: HOW TO UPDATE THESE !!!
# -----------------------------------
# (tests break with new version of aiohttp/botocore)
#
# 1) Adding support for more versions of aiohttp/botocore
#    In this scenario you need to ensure that aiobotocore supports the changes
#    that broke these tests along with the old versions of the libraries
#    and APPEND to the set of hashes that we support for each object you
#    validated.
# 2) Bumping up the base version of aiohttp/botocore that we support
#    In this scenario ensure aiobotocore supports the new version of the libs
#    and REPLACE all entries with the current hashes with the new libs.

# REPLACE = backwards incompatible change
# APPEND = officially supporting more versions of botocore/aiohttp

# If you're changing these, most likely need to update setup.py as well.
_API_DIGESTS = {
    ClientArgsCreator: {'c316001114ff0b91900004e2fc56b71a07509f16'},
    ClientCreator: {'f68202aca8c908d14b3d7b2446875d297c46671b'},
    BaseClient: {'63fc3b6ae4cdb265b5363c093832890074f52e18'},
    Config: {'b84933bb901b4f18641dffe75cc62d55affd390a'},
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},
    Endpoint: {'29827aaa421d462ab7b9e200d7203ba9e412633c'},
    EndpointCreator: {'633337fe0bda97e57c7f0b9596c5a158a03e8e36'},
    PageIterator: {'5a14db3ee7bc8773974b36cfdb714649b17a6a42'},
    Session: {'a8132407e250b652c89db15a9002f41664638a3f'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    NormalizedOperationMethod: {'ee88834b123c6c77dfea0b4208308cd507a6ba36'},
}


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_patches():
    print("Botocore version: {} aiohttp version: {}".format(
        botocore.__version__, aiohttp.__version__))

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
        traces=None,
        loop=event_loop,
        session=None)
    resp.status_code = 500
    assert resp.status_code == 500
