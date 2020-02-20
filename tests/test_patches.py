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
from botocore.eventstream import EventStream
from botocore.parsers import ResponseParserFactory, PROTOCOL_PARSERS, \
    ResponseParser


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
    ClientArgsCreator: {'917d7f5a85d2497c0ee0d7f368a4e6a723bb534a'},
    ClientCreator._create_client_class: {'5e493d069eedbf314e40e12a7886bbdbcf194335'},
    ClientCreator._get_client_args: {'555e1e41f93df7558c8305a60466681e3a267ef3'},
    BaseClient: {'d2e69f0184ae83df5b82284194ed1b72191f0161'},
    Config: {'2dcc44190a3dc2a4b26ab0ed9410daefcd7c93c1'},
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},
    Endpoint: {'6839b062c5223d94aaf894dce6ade8606c388b4f'},
    EndpointCreator: {'633337fe0bda97e57c7f0b9596c5a158a03e8e36'},
    PageIterator: {'0409f7878b3493566be5761f5799ed93563f3e20'},
    Session: {'36b41d75c22bc86afc889a56de92f1f95f836e20'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    NormalizedOperationMethod: {'ee88834b123c6c77dfea0b4208308cd507a6ba36'},
    EventStream: {'0e68633755a7dd4ff79c6d7ca778800a7bc86d3b'},
    ResponseParserFactory: {'db484fd7e743611b9657c8f1acc84e76597e96b7'},
    ResponseParser: {'d16826f7e815a62d7a5ca0d2ca5d936c64e0da88'},

}

_PROTOCOL_PARSER_CONTENT = {'ec2', 'query', 'json', 'rest-json', 'rest-xml'}


@pytest.mark.moto
def test_protocol_parsers():
    # Check that no new parsers have been added
    current_parsers = set(PROTOCOL_PARSERS.keys())
    assert current_parsers == _PROTOCOL_PARSER_CONTENT


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
