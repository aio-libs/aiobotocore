import pytest
import hashlib
from dill.source import getsource

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
    # ( <=1.5.70, >=1.5.71)
    ClientArgsCreator: {'60b8b70358d25dcce1ad74b2f5f0b4830d5749a3',
                        '813098fb076fc4739f7f6ec335960845e119f5f4'},
    ClientCreator: {'a8c12a6b933fa22477cd4d168a96443be002ae79',
                    '1533fe9ffa395f8555ffad0fb5b6f5294d2c0f30'},
    BaseClient: {'a91ffdb8d0c7cb2dfd63a4332a0a7a76e76cef28'},
    Config: {'c6bdc8f47c90e114d406ecab3fcfbc6e4d034279'},
    convert_to_response_dict: {'ed634b3f0c24f8858aee8ed745051397270b1e46'},
    Endpoint: {'7aa956cf3f28f573384dbaeb2f819b0a05724e65'},
    EndpointCreator: {'63fb01d5cad63d96d0fdd2f1764df51bc1197ff8'},
    PageIterator: {'21fc6a86071177b55761af9f723ade5b8a23d153'},
    Session: {'0203d047c8e23e648da1c50f9e6fb5dd53b797f7'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
}


# NOTE: this doesn't require moto but needs to be marked to run with coverage
@pytest.mark.moto
def test_patches():
    for obj, digests in _READ_TIMEOUT_DIGESTS.items():
        digest = hashlib.sha1(getsource(obj).encode('utf-8')).hexdigest()
        assert digest in digests, \
            "Digest of {} not found int: {}".format(obj.__name__, digests)

    for obj, digests in _API_DIGESTS.items():
        digest = hashlib.sha1(getsource(obj).encode('utf-8')).hexdigest()
        assert digest in digests, \
            "Digest of {} not found int: {}".format(obj.__name__, digests)
