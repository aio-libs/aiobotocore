import asyncio
import hashlib
from dill.source import getsource
from itertools import chain

import pytest
from yarl import URL

from aiobotocore.endpoint import ClientResponseProxy

import aiohttp
from aiohttp.client import ClientResponse
import botocore
from botocore.args import ClientArgsCreator
from botocore.client import ClientCreator, BaseClient, Config
from botocore.endpoint import convert_to_response_dict, Endpoint, \
    EndpointCreator
from botocore.paginate import PageIterator, ResultKeyIterator
from botocore.session import Session, get_session
from botocore.waiter import NormalizedOperationMethod, Waiter, \
    create_waiter_with_client
from botocore.eventstream import EventStream
from botocore.parsers import ResponseParserFactory, PROTOCOL_PARSERS, \
    RestXMLParser, EC2QueryParser, QueryParser, JSONParser, RestJSONParser
from botocore.response import StreamingBody


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
    # args.py
    ClientArgsCreator.get_client_args: {'e3a44e6f50159e8e31c3d76f5e8a1110dda495fa'},

    # client.py
    ClientCreator._create_client_class: {'5e493d069eedbf314e40e12a7886bbdbcf194335'},
    ClientCreator._get_client_args: {'555e1e41f93df7558c8305a60466681e3a267ef3'},

    BaseClient._make_api_call: {'0c59329d4c8a55b88250b512b5e69239c42246fb'},
    BaseClient._make_request: {'033a386f7d1025522bea7f2bbca85edc5c8aafd2'},
    BaseClient.get_paginator: {'c69885f5f73fae048c0b93b43bbfcd1f9c6168b8'},
    BaseClient.get_waiter: {'23d57598555bfbc4c6e7ec93406d05771f108d9e'},

    # config.py
    Config.merge: {'c3dd8c3ffe0da86953ceba4a35267dfb79c6a2c8'},
    Config: {'2dcc44190a3dc2a4b26ab0ed9410daefcd7c93c1'},

    # endpoint.py
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},

    Endpoint._send_request: {'50ab33d6f16e75594d01ab1c2ec6b7c7903798db'},
    Endpoint._get_response: {'46c3a8cb4ff7672b75193ce5571dbea48aa9da75'},
    Endpoint._do_get_response: {'df29f099d26dc057834c7b25d3b5217f1f7acbe4'},
    Endpoint._needs_retry: {'0f40f52d8c90c6e10b4c9e1c4a5ca00ef2c72850'},
    Endpoint._send: {'644c7e5bb88fecaa0b2a204411f8c7e69cc90bf1'},

    EndpointCreator.create_endpoint: {'36065caa2398573be229bee500e27303bc362348'},

    # eventstream.py
    EventStream._create_raw_event_generator: {
        'cc101f3ca2bca4f14ccd6b385af900a15f96967b'},
    EventStream.__iter__: {'8a9b454943f8ef6e81f5794d641adddd1fdd5248'},

    # paginate.py
    PageIterator.__iter__: {'56b3a1e30f488e2f1f5d5309db42fd5ad8a3895d'},
    PageIterator.result_key_iters: {'04d3c647bd98caba3687df80e650fea517a0068e'},
    PageIterator.build_full_result: {'afe8cd8daad2cf32ae34f877985ab79501bf7742'},
    ResultKeyIterator: {'f71d98959ccda5e05e35cf3cf224fbc9310d33bb'},

    # parsers.py
    ResponseParserFactory.create_parser: {'5cf11c9acecd1f60a013f6facbe0f294daa3f390'},
    RestXMLParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    EC2QueryParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    QueryParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    JSONParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    RestJSONParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},

    # response.py
    StreamingBody: {'bb4d872649b0c118c9a3d5e44961e1bea92eb79c'},

    # session.py
    Session.__init__: {'ccf156a76beda3425fb54363f3b2718dc0445f6d'},
    Session.create_client: {'36f4e718fc4bada66808c2f98fa71835c09076f7'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},

    # waiter.py
    NormalizedOperationMethod.__call__: {'79723632d023739aa19c8a899bc2b814b8ab12ff'},
    Waiter.wait: {'5502a89ed740fb5d6238a6f72a3a08efc1a9f43b'},
    create_waiter_with_client: {'c3d12c9a4293105cc8c2ecfc7e69a2152ad564de'},
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

    success = True
    for obj, digests in chain(_AIOHTTP_DIGESTS.items(), _API_DIGESTS.items()):
        digest = hashlib.sha1(getsource(obj).encode('utf-8')).hexdigest()
        if digest not in digests:
            print("Digest of {}:{} not found in: {}".format(
                obj.__qualname__, digest, digests))
            success = False

    assert success


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
@pytest.mark.asyncio
async def test_set_status_code():
    resp = ClientResponseProxy(
        'GET', URL('http://foo/bar'),
        loop=asyncio.get_event_loop(),
        writer=None, continue100=None, timer=None,
        request_info=None,
        traces=None,
        session=None)
    resp.status_code = 500
    assert resp.status_code == 500
