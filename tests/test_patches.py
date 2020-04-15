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
from botocore.signers import RequestSigner, add_generate_presigned_url, \
    generate_presigned_url, S3PostPresigner, add_generate_presigned_post, \
    generate_presigned_post
from botocore.hooks import EventAliaser, HierarchicalEmitter
from botocore.utils import ContainerMetadataFetcher, IMDSFetcher, \
    InstanceMetadataFetcher
from botocore.credentials import Credentials, RefreshableCredentials, \
    CachedCredentialFetcher, AssumeRoleCredentialFetcher, EnvProvider, \
    ContainerProvider, InstanceMetadataProvider, ProfileProviderBuilder, \
    ConfigProvider, SharedCredentialProvider, ProcessProvider, CredentialResolver, \
    AssumeRoleWithWebIdentityProvider, AssumeRoleProvider, \
    CanonicalNameCredentialSourcer, BotoProvider, OriginalEC2Provider, \
    create_credential_resolver, get_credentials, create_mfa_serial_refresher, \
    AssumeRoleWithWebIdentityCredentialFetcher

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
    ClientCreator.create_client: {'ee63a3d60b5917879cb644c1b0aa3fe34538b915'},
    ClientCreator._create_client_class: {'5e493d069eedbf314e40e12a7886bbdbcf194335'},
    ClientCreator._get_client_args: {'555e1e41f93df7558c8305a60466681e3a267ef3'},

    BaseClient._make_api_call: {'0c59329d4c8a55b88250b512b5e69239c42246fb'},
    BaseClient._make_request: {'033a386f7d1025522bea7f2bbca85edc5c8aafd2'},
    BaseClient._convert_to_request_dict: {'0071c2a37c3c696d9b0fba5f54b2985489c76b78'},
    BaseClient._emit_api_params: {'2bfadaaa70671b63c50b1beed6d6c66e85813e9b'},
    BaseClient.get_paginator: {'c69885f5f73fae048c0b93b43bbfcd1f9c6168b8'},
    BaseClient.get_waiter: {'23d57598555bfbc4c6e7ec93406d05771f108d9e'},
    BaseClient.__getattr__: {'63f8ad095789d47880867f18537a277195845111'},

    # config.py
    Config.merge: {'c3dd8c3ffe0da86953ceba4a35267dfb79c6a2c8'},
    Config: {'2dcc44190a3dc2a4b26ab0ed9410daefcd7c93c1'},

    # credentials.py
    create_mfa_serial_refresher: {'180b81fc40c91d1cf40de1a28e32ae7d601e1d50'},
    Credentials.get_frozen_credentials: {'08af57df08ee9953e440aa7aca58137ed936cdb6'},
    RefreshableCredentials.__init__: {'c685fd2c62eb60096fdf8bb885fb642df1819f7f'},
    # We've overridden some properties
    RefreshableCredentials.__dict__['access_key'].fset:
        {'edc4a25baef877a9662f68cd9ccefcd33a81bab7'},
    RefreshableCredentials.__dict__['access_key'].fget:
        {'f6c823210099db99dd343d9e1fae6d4eb5aa5fce'},
    RefreshableCredentials.__dict__['secret_key'].fset:
        {'b19fe41d66822c72bd6ae2e60de5c5d27367868a'},
    RefreshableCredentials.__dict__['secret_key'].fget:
        {'3e27331a037549104b8669e225bbbb2c465a16d4'},
    RefreshableCredentials.__dict__['token'].fset:
        {'1f8a308d4bf21e666f8054a0546e91541661da7b'},
    RefreshableCredentials.__dict__['token'].fget:
        {'005c1b44b616f37739ce9276352e4e83644d8220'},
    RefreshableCredentials._refresh: {'f4759b7ef0d1f0d8af07855dcd9ca49ef12c2e7b'},
    RefreshableCredentials._protected_refresh:
        {'432409f81601dbeea9ec187d433d190ab7c5ab2f'},
    RefreshableCredentials.get_frozen_credentials:
        {'f661c84a8b759786e011f0b1e8a468a0c6294e36'},

    CachedCredentialFetcher._get_credentials:
        {'02a7d13599d972e3f258d2b53f87eeda4cc3e3a4'},
    CachedCredentialFetcher.fetch_credentials:
        {'0dd2986a4cbb38764ec747075306a33117e86c3d'},
    CachedCredentialFetcher._get_cached_credentials:
        {'a9f8c348d226e62122972da9ccc025365b6803d6'},
    AssumeRoleCredentialFetcher._get_credentials:
        {'5c575634bc0a713c10e5668f28fbfa8779d5a1da'},
    AssumeRoleCredentialFetcher._create_client:
        {'27c76f07bd43e665899ca8d21b6ba2038b276fbb'},
    # Referenced by AioAssumeRoleWithWebIdentityCredentialFetcher
    AssumeRoleWithWebIdentityCredentialFetcher.__init__:
        {'85c022a7237a3500ca973b2f7f91bffe894e4577'},
    AssumeRoleWithWebIdentityCredentialFetcher._get_credentials:
        {'02eba9d4e846474910cb076710070348e395a819'},
    AssumeRoleWithWebIdentityCredentialFetcher._assume_role_kwargs:
        {'8fb4fefe8664b7d82a67e0fd6d6812c1c8d92285'},
    # Ensure that the load method doesn't do anything we should asyncify
    EnvProvider.load: {'07cff5032b39b568505779774a1ca66efc513abb'},

    ContainerProvider.__init__: {'ea6aafb2e12730066af930fb5a27f7659c1736a1'},
    ContainerProvider.load: {'57c35569050b45c1e9e33fcdb3b49da9e342fdcf'},
    ContainerProvider._retrieve_or_fail:
        {'7c14f1cdee07217f847a71068866bdd10c3fa0fa'},
    ContainerProvider._create_fetcher:
        {'09a3ffded0fc20a574f3b34fa432a1569d5e729f'},
    InstanceMetadataProvider.load: {'4a27eb94fe220fba2b46c97bdd9e16de199ce004'},
    ProfileProviderBuilder._create_process_provider:
        {'c5eea47bcfc449a6d73a9892bd0e1897f6be0c20'},
    ProfileProviderBuilder._create_shared_credential_provider:
        {'33f99c6a0ef71a92b0c52ccc59c8ca7e33fa0890'},
    ProfileProviderBuilder._create_config_provider:
        {'f9a40d4211f6e663ba2ae9682fba5306152178c5'},
    ProfileProviderBuilder._create_web_identity_provider:
        {'0907c1ad5573bc5c0fc87efb601a6c4c3fcf34ae'},
    ConfigProvider.load: {'8fb32140086dce65fa28be8edd3ac0d22698c3ae'},
    SharedCredentialProvider.load: {'c0be1fe376d25952461ca18d9bef4b4340203441'},
    ProcessProvider.__init__: {'2e870ec0c6b0bc8483fa9b1159ef68bbd7a12c56'},
    ProcessProvider.load: {'aac90e2c8823939f09936b9c883e67503128e438'},
    ProcessProvider._retrieve_credentials_using:
        {'ffc27c7cba0e37cf6db3a3eacfd54be8bd99d3a9'},
    CredentialResolver.load_credentials:
        {'ef31ba8817f84c1f61f36259da1cc6e597b8625a'},
    AssumeRoleWithWebIdentityProvider.load:
        {'8f48f6cadf08a09cf5a22b1cc668e60bc4ea389d'},
    AssumeRoleWithWebIdentityProvider._assume_role_with_web_identity:
        {'32c9d720ab5f12054583758b5cd5d287f652ccd3'},
    AssumeRoleProvider.load: {'ee9ddb43e25eb1105185253c0963a2f5add49a95'},
    AssumeRoleProvider._load_creds_via_assume_role:
        {'9fdba45a8dd16b885dea7c1fafc7d02609870fa7'},
    AssumeRoleProvider._resolve_source_credentials:
        {'105c0c011e23d76a3b8bd3d9b91b6d945c8307a1'},
    AssumeRoleProvider._resolve_credentials_from_profile:
        {'402a1a6b3e0a29c234b7883e5b855110eb655830'},
    AssumeRoleProvider._resolve_static_credentials_from_profile:
        {'58f04986bb1027d548212b7769034e5dae5cc30f'},
    AssumeRoleProvider._resolve_credentials_from_source:
        {'6f76ae62f477279a2297565f80a5cfbe5ea30eaf'},
    CanonicalNameCredentialSourcer.source_credentials:
        {'602930a78e0e64e3b313a046aab5edc3bcf5c2d9'},
    CanonicalNameCredentialSourcer._get_provider:
        {'c028b9776383cc566be10999745b6082f458d902'},
    BotoProvider.load: {'9351b8565c2c969937963fc1d3fbc8b3b6d8ccc1'},
    OriginalEC2Provider.load: {'bde9af019f01acf3848a6eda125338b2c588c1ab'},
    create_credential_resolver: {'5ff7fe49d7636b795a50202ff5c089611f4e27c1'},
    get_credentials: {'ff0c735a388ac8dd7fe300a32c1e36cdf33c0f56'},

    # endpoint.py
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},

    Endpoint.create_request: {'4ccc14de2fd52f5c60017e55ff8e5b78bbaabcec'},
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

    # hooks.py
    HierarchicalEmitter._emit: {'5d9a6b1aea1323667a9310e707a9f0a006f8f6e8'},
    HierarchicalEmitter.emit_until_response:
        {'23670e04e0b09a9575c7533442bca1b2972ade82'},
    EventAliaser.emit_until_response: {'0d635bf7ae5022b1fdde891cd9a91cd4c449fd49'},

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
    Session._register_response_parser_factory:
        {'d6cd5a8b1b473b0ec3b71db5f621acfb12cc412c'},
    Session.create_client: {'36f4e718fc4bada66808c2f98fa71835c09076f7'},
    Session._create_credential_resolver: {'87e98d201c72d06f7fbdb4ebee2dce1c09de0fb2'},
    Session.get_credentials: {'c0de970743b6b9dd91b5a71031db8a495fde53e4'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    Session.get_service_data: {'e28f2de9ebaf13214f1606d33349dfa8e2555923'},
    Session.get_service_model: {'1c8f93e6fb9913e859e43aea9bc2546edbea8365'},
    Session.get_available_regions: {'bc455d24d98fbc112ff22325ebfd12a6773cb7d4'},

    # signers.py
    RequestSigner.handler: {'371909df136a0964ef7469a63d25149176c2b442'},
    RequestSigner.sign: {'7df841d3df3f4015763523c1932652aef754287a'},
    RequestSigner.get_auth: {'4f8099bef30f9a72fa3bcaa1bd3d22c4fbd224a8'},
    RequestSigner.get_auth_instance: {'4f8099bef30f9a72fa3bcaa1bd3d22c4fbd224a8'},
    RequestSigner._choose_signer: {'d1e0e3196ada449d3ae0ec09b8ae9b5868c50d4e'},
    RequestSigner.generate_presigned_url: {'2acffdfd926b7b6f6cc4b70b90c0587e7f424888'},
    add_generate_presigned_url: {'5820f74ac46b004eb79e00eea1adc467bcf4defe'},
    generate_presigned_url: {'9c471f957210c0a71a11f5c73be9fed844ecb5bb'},
    S3PostPresigner.generate_presigned_post:
        {'b91d50bae4122d7ab540653865ec9294520ac0e1'},
    add_generate_presigned_post: {'e30360f2bd893fabf47f5cdb04b0de420ccd414d'},
    generate_presigned_post: {'85e9ebe0412cb10716bf84a1533798882f3fc79f'},

    # utils.py
    ContainerMetadataFetcher.__init__:
        {'46d90a7249ba8389feb487779b0a02e6faa98e57'},
    ContainerMetadataFetcher.retrieve_full_uri:
        {'2c7080f7d6ee5a3dacc1b690945c045dba1b1d21'},
    ContainerMetadataFetcher.retrieve_uri:
        {'4ee8aa704cf0a378d68ef9a7b375a1aa8840b000'},
    ContainerMetadataFetcher._retrieve_credentials:
        {'f5294f9f811cb3cc370e4824ca106269ea1f44f9'},
    ContainerMetadataFetcher._get_response:
        {'7e5acdd2cf0167a047e3d5ee1439565a2f79f6a6'},
    # Overrided session and dealing with proxy support
    IMDSFetcher.__init__: {'690e37140ccdcd67c7a85ce5d36331491a79954e'},
    IMDSFetcher._get_request: {'96a0e580cab5a21deb4d2cd7e904aa17d5e1e504'},
    IMDSFetcher._fetch_metadata_token: {'4fdad673b4997b1268c6d9dff09a4b99c1cb5e0d'},

    InstanceMetadataFetcher.retrieve_iam_role_credentials:
        {'76737f6add82a1b9a0dc590cf10bfac0c7026a2e'},
    InstanceMetadataFetcher._get_iam_role: {'80073d7adc9fb604bc6235af87241f5efc296ad7'},
    InstanceMetadataFetcher._get_credentials:
        {'1a64f59a3ca70b83700bd14deeac25af14100d58'},

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
