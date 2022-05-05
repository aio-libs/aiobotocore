import hashlib
from dill.source import getsource
from itertools import chain

import pytest

import aiohttp
from aiohttp.client import ClientResponse
import botocore
from botocore.args import ClientArgsCreator
from botocore.client import ClientCreator, BaseClient, Config
from botocore.configprovider import SmartDefaultsConfigStoreFactory
from botocore.endpoint import convert_to_response_dict, Endpoint, \
    EndpointCreator
from botocore.paginate import PageIterator, ResultKeyIterator
from botocore.session import Session, get_session
from botocore.waiter import NormalizedOperationMethod, Waiter, \
    create_waiter_with_client
from botocore.eventstream import EventStream
from botocore.parsers import ResponseParserFactory, PROTOCOL_PARSERS, \
    RestXMLParser, EC2QueryParser, QueryParser, JSONParser, RestJSONParser, \
    create_parser
from botocore.response import StreamingBody, get_response
from botocore.signers import RequestSigner, add_generate_presigned_url, \
    generate_presigned_url, S3PostPresigner, add_generate_presigned_post, \
    generate_presigned_post, generate_db_auth_token, add_generate_db_auth_token
from botocore.hooks import EventAliaser, HierarchicalEmitter
from botocore.utils import ContainerMetadataFetcher, IMDSFetcher, \
    InstanceMetadataFetcher, S3RegionRedirector, InstanceMetadataRegionFetcher, \
    IMDSRegionProvider
from botocore.credentials import Credentials, RefreshableCredentials, \
    CachedCredentialFetcher, AssumeRoleCredentialFetcher, EnvProvider, \
    ContainerProvider, InstanceMetadataProvider, ProfileProviderBuilder, \
    ConfigProvider, SharedCredentialProvider, ProcessProvider, CredentialResolver, \
    AssumeRoleWithWebIdentityProvider, AssumeRoleProvider, \
    CanonicalNameCredentialSourcer, BotoProvider, OriginalEC2Provider, \
    create_credential_resolver, get_credentials, create_mfa_serial_refresher, \
    AssumeRoleWithWebIdentityCredentialFetcher, SSOCredentialFetcher, SSOProvider
from botocore.handlers import inject_presigned_url_ec2, inject_presigned_url_rds, \
    parse_get_bucket_location, check_for_200_error, _looks_like_special_case_error
from botocore.httpsession import URLLib3Session
from botocore.discovery import EndpointDiscoveryManager, EndpointDiscoveryHandler
from botocore.retries import adaptive, special
from botocore.retries.bucket import TokenBucket
from botocore import retryhandler
from botocore.retries import standard
from botocore.awsrequest import AWSResponse
from botocore.httpchecksum import handle_checksum_body, _handle_bytes_response


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
    ClientResponse: {'e178726065b609c69a1c02e8bb78f22efce90792',
                     '225e8033bfcff8cccbc2e975d7bd0c7993f14366'},
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
    ClientArgsCreator.get_client_args: {'a98b0bf9fe62f79b533b87664183c8886bc6816b'},

    # client.py
    ClientCreator.create_client: {'5cc47860c371ecd83b2e62c58bef590085cb07e0'},
    ClientCreator._create_client_class: {'5e493d069eedbf314e40e12a7886bbdbcf194335'},
    ClientCreator._register_endpoint_discovery:
        {'2eb9009d83a3999c77ecf2fd3335dab94348182e'},
    ClientCreator._get_client_args: {'555e1e41f93df7558c8305a60466681e3a267ef3'},
    ClientCreator._register_s3_events: {'accf68c9e3e45b114310e8c635270ccb5fc4926e'},
    ClientCreator._register_retries: {'16d3064142e5f9e45b0094bbfabf7be30183f255'},
    ClientCreator._register_v2_adaptive_retries:
        {'665ecd77d36a5abedffb746d83a44bb0a64c660a'},
    ClientCreator._register_v2_standard_retries:
        {'9ec4ff68599544b4f46067b3783287862d38fb50'},
    ClientCreator._register_legacy_retries:
        {'7dbd1a9d045b3d4f5bf830664f17c7bc610ee3a3'},

    BaseClient._make_api_call: {'6517c7ead41bf0c70f38bb70666bffd21835ed72'},
    BaseClient._make_request: {'033a386f7d1025522bea7f2bbca85edc5c8aafd2'},
    BaseClient._convert_to_request_dict: {'0071c2a37c3c696d9b0fba5f54b2985489c76b78'},
    BaseClient._emit_api_params: {'2bfadaaa70671b63c50b1beed6d6c66e85813e9b'},
    BaseClient.get_paginator: {'c69885f5f73fae048c0b93b43bbfcd1f9c6168b8'},
    BaseClient.get_waiter: {'23d57598555bfbc4c6e7ec93406d05771f108d9e'},
    BaseClient.__getattr__: {'63f8ad095789d47880867f18537a277195845111'},

    # config.py
    Config.merge: {'c3dd8c3ffe0da86953ceba4a35267dfb79c6a2c8'},
    Config: {'1fb5fb546abe4970c98560b9f869339322930cdc'},

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
        {'c1b6bcb5d1d145ef2980037f2c24385151e3acab'},
    RefreshableCredentials.get_frozen_credentials:
        {'f661c84a8b759786e011f0b1e8a468a0c6294e36'},
    SSOCredentialFetcher:
        {'d68353a0d2c291d5742f134d28ae1e1419faa4c6'},
    SSOProvider.load:
        {'f43d79e1520b2a7b7ef85cd537f41e19d4bce806'},
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
    ProfileProviderBuilder._create_sso_provider:
        {'258e6d07bdf40ea2c7551bae0cd6e1ab58e4e502'},
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
    create_credential_resolver: {'177ad331d4b527b9aae765d90e2f17badefeb4a8'},
    get_credentials: {'ff0c735a388ac8dd7fe300a32c1e36cdf33c0f56'},

    # configprovider.py
    SmartDefaultsConfigStoreFactory.merge_smart_defaults:
        {'e1049d34cba3197b4e70dabbbe59e17686fa90f9'},
    SmartDefaultsConfigStoreFactory.resolve_auto_mode:
        {'61e749ec045bb0c670bcbc9846b4cfc16cde5718'},

    # endpoint.py
    convert_to_response_dict: {'2c73c059fa63552115314b079ae8cbf5c4e78da0'},

    Endpoint.create_request: {'4ccc14de2fd52f5c60017e55ff8e5b78bbaabcec'},
    Endpoint._send_request: {'214fa3a0e72f3877cef915c8429d40729775f0cf'},
    Endpoint._get_response: {'6803c16fb6576ea18d9e3d8ffb2e9f3874d9b8ee'},
    Endpoint._do_get_response: {'91370e4a034ec61fa8090fe9442cfafe9b63c6cc'},
    Endpoint._needs_retry: {'0f40f52d8c90c6e10b4c9e1c4a5ca00ef2c72850'},
    Endpoint._send: {'644c7e5bb88fecaa0b2a204411f8c7e69cc90bf1'},
    Endpoint._add_modeled_error_fields: {'1eefcfacbe9a2c3700c61982e565ce6c4cf1ea3a'},

    EndpointCreator.create_endpoint: {'77a36b0fdc2e4ae7c421849843b93b4dcae5e06f'},

    # eventstream.py
    EventStream._create_raw_event_generator: {
        'cc101f3ca2bca4f14ccd6b385af900a15f96967b'},
    EventStream.__iter__: {'8a9b454943f8ef6e81f5794d641adddd1fdd5248'},
    EventStream.get_initial_response: {'aed648305970c90bb5d1e31f6fe5ff12cf6a2a06'},

    # hooks.py
    HierarchicalEmitter._emit: {'5d9a6b1aea1323667a9310e707a9f0a006f8f6e8'},
    HierarchicalEmitter.emit_until_response:
        {'23670e04e0b09a9575c7533442bca1b2972ade82'},
    HierarchicalEmitter._verify_and_register:
        {'aa14572fd9d42b83793d4a9d61c680e37761d762'},

    EventAliaser.emit_until_response: {'0d635bf7ae5022b1fdde891cd9a91cd4c449fd49'},

    # paginate.py
    PageIterator.__iter__: {'a56ec9b28dba7e48936d7164b5ea0e3a0fc0287d'},
    PageIterator.result_key_iters: {'04d3c647bd98caba3687df80e650fea517a0068e'},
    PageIterator.build_full_result: {'afe8cd8daad2cf32ae34f877985ab79501bf7742'},
    ResultKeyIterator: {'f71d98959ccda5e05e35cf3cf224fbc9310d33bb'},

    # parsers.py
    ResponseParserFactory.create_parser: {'5cf11c9acecd1f60a013f6facbe0f294daa3f390'},
    RestXMLParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    EC2QueryParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    QueryParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    JSONParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    JSONParser._do_parse: {'9c3d5832e6c55a87630128cc8b9121579ef4a708'},
    JSONParser._handle_event_stream: {'3cf7bb1ecff0d72bafd7e7fd6625595b4060abd6'},

    # NOTE: if this hits we need to change our ResponseParser impl in JSONParser
    JSONParser.parse: {'38231a2fffddfa6e91c56c2a01134459e365beb3'},

    RestJSONParser._create_event_stream: {'0564ba55383a71cc1ba3e5be7110549d7e9992f5'},
    create_parser: {'37e9f1c3b60de17f477a9b79eae8e1acaa7c89d7'},

    # response.py
    StreamingBody: {'a177edffd0c4ec72f1bb4b9e09ea33bc6d37b248'},
    get_response: {'f31b478792a5e0502f142daca881b69955e5c11d'},

    # session.py
    Session.__init__: {'ccf156a76beda3425fb54363f3b2718dc0445f6d'},
    Session._register_response_parser_factory:
        {'d6cd5a8b1b473b0ec3b71db5f621acfb12cc412c'},
    Session.create_client: {'7e0c40c06d3fede4ebbd862f1d6e51118c4a1ff0'},
    Session._create_credential_resolver: {'87e98d201c72d06f7fbdb4ebee2dce1c09de0fb2'},
    Session.get_credentials: {'c0de970743b6b9dd91b5a71031db8a495fde53e4'},
    get_session: {'c47d588f5da9b8bde81ccc26eaef3aee19ddd901'},
    Session.get_service_data: {'e28f2de9ebaf13214f1606d33349dfa8e2555923'},
    Session.get_service_model: {'1c8f93e6fb9913e859e43aea9bc2546edbea8365'},
    Session.get_available_regions: {'bc455d24d98fbc112ff22325ebfd12a6773cb7d4'},
    Session._register_smart_defaults_factory:
        {'24ab10e4751ada800dde24d40d1d105be76a0a14'},

    # signers.py
    RequestSigner.handler: {'371909df136a0964ef7469a63d25149176c2b442'},
    RequestSigner.sign: {'a07e4caab222bf9375036b1fafaf021ccb5b2bf3'},
    RequestSigner.get_auth: {'4f8099bef30f9a72fa3bcaa1bd3d22c4fbd224a8'},
    RequestSigner.get_auth_instance: {'4f8099bef30f9a72fa3bcaa1bd3d22c4fbd224a8'},
    RequestSigner._choose_signer: {'d1e0e3196ada449d3ae0ec09b8ae9b5868c50d4e'},
    RequestSigner.generate_presigned_url: {'2acffdfd926b7b6f6cc4b70b90c0587e7f424888'},
    add_generate_presigned_url: {'5820f74ac46b004eb79e00eea1adc467bcf4defe'},
    generate_presigned_url: {'9c471f957210c0a71a11f5c73be9fed844ecb5bb'},
    S3PostPresigner.generate_presigned_post:
        {'b91d50bae4122d7ab540653865ec9294520ac0e1'},
    add_generate_presigned_post: {'e30360f2bd893fabf47f5cdb04b0de420ccd414d'},
    generate_presigned_post: {'e9756488cf1ceb68d23b36688f3d0767505f3c77'},
    add_generate_db_auth_token: {'f61014e6fac4b5c7ee7ac2d2bec15fb16fa9fbe5'},
    generate_db_auth_token: {'5f5a758458c007107a23124192339f747472dc75'},

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

    IMDSFetcher.__init__: {'a0766a5ba7dde9c26f3c51eb38d73f8e6087d492'},
    IMDSFetcher._get_request: {'d06ba6890b94c819e79e27ac819454b28f704535'},
    IMDSFetcher._fetch_metadata_token: {'c162c832ec24082cd2054945382d8dc6a1ec5e7b'},
    IMDSFetcher._default_retry: {'d1fa834cedfc7a2bf9957ba528eed24f600f7ef6'},
    IMDSFetcher._is_non_ok_response: {'448b80545b1946ec44ff19ebca8d4993872a6281'},
    IMDSFetcher._is_empty: {'241b141c9c352a4ef72964f8399d46cbe9a5aebc'},
    IMDSFetcher._log_imds_response: {'f1e09ad248feb167f55b11bbae735ea0e2c7b446'},

    InstanceMetadataFetcher.retrieve_iam_role_credentials:
        {'76737f6add82a1b9a0dc590cf10bfac0c7026a2e'},
    InstanceMetadataFetcher._get_iam_role:
        {'80073d7adc9fb604bc6235af87241f5efc296ad7'},
    InstanceMetadataFetcher._get_credentials:
        {'1a64f59a3ca70b83700bd14deeac25af14100d58'},
    InstanceMetadataFetcher._is_invalid_json:
        {'97818b51182a2507c99876a40155adda0451dd82'},
    InstanceMetadataFetcher._needs_retry_for_role_name:
        {'0f1034c9de5be2d79a584e1e057b8df5b39f4514'},
    InstanceMetadataFetcher._needs_retry_for_credentials:
        {'977be4286b42916779ade4c20472ec3a6a26c90d'},

    S3RegionRedirector.redirect_from_error:
        {'f6f765431145a9bed8e73e6a3dbc7b0d6ae5f738'},
    S3RegionRedirector.get_bucket_region:
        {'b5bbc8b010576668dc2812d657c4b48af79e8f99'},
    InstanceMetadataRegionFetcher.retrieve_region:
        {'e916aeb4a28a265224a21006dce1d443cd1207c4'},
    InstanceMetadataRegionFetcher._get_region:
        {'73f8c60d21aae765db3a473a527c846e0108291f'},
    IMDSRegionProvider.provide:
        {'09d1b70bc1dd7a37cb9ffd437acd71283b9142e9'},
    IMDSRegionProvider._get_instance_metadata_region:
        {'4631ced79cff143de5d3fdf03cd69720778f141b'},
    IMDSRegionProvider._create_fetcher:
        {'28b711326769d03a282558066058cd85b1cb4568'},

    # waiter.py
    NormalizedOperationMethod.__call__: {'79723632d023739aa19c8a899bc2b814b8ab12ff'},
    Waiter.wait: {'a9fa6e3b1210929b9e3887abff90aeb451383547'},
    create_waiter_with_client: {'c3d12c9a4293105cc8c2ecfc7e69a2152ad564de'},

    # handlers.py
    inject_presigned_url_rds: {'5a34e1666d84f6229c54a59bffb69d46e8117b3a'},
    inject_presigned_url_ec2: {'37fad2d9c53ca4f1783e32799fa8f70930f44c23'},
    parse_get_bucket_location: {'dde31b9fe4447ed6eb9b8c26ab14cc2bd3ae2c64'},
    check_for_200_error: {'94005c964d034e68bb2f079e89c93115c1f11aad'},
    _looks_like_special_case_error: {'adcf7c6f77aa123bd94e96ef0beb4ba548e55086'},

    # httpsession.py
    URLLib3Session: {'5adede4ba9d2a80e776bfeb71127656fafff91d7'},

    EndpointDiscoveryHandler.discover_endpoint:
        {'d87eff9008356a6aaa9b7078f23ba7a9ff0c7a60'},
    EndpointDiscoveryManager.describe_endpoint:
        {'b2f1b29177cf30f299e61b85ddec09eaa070e54e'},
    EndpointDiscoveryManager._refresh_current_endpoints:
        {'f8a51047c8f395d9458a904e778a3ac156a11911'},

    # retries/adaptive.py
    # See comments in AsyncTokenBucket: we completely replace the ClientRateLimiter
    # implementation from botocore.
    adaptive.ClientRateLimiter: {'d4ba74b924cdccf705adeb89f2c1885b4d21ce02'},
    adaptive.register_retry_handler: {'d662512878511e72d1202d880ae181be6a5f9d37'},

    # retries/standard.py
    standard.register_retry_handler: {'8d464a753335ce7457c5eea73e80d9a224fe7f21'},
    standard.RetryHandler.needs_retry: {'2dfc4c2d2efcd5ca00ae84ccdca4ab070d831e22'},
    standard.RetryPolicy.should_retry: {'b30eadcb94dadcdb90a5810cdeb2e3a0bc0c74c9'},
    standard.StandardRetryConditions.__init__:
        {'82f00342fb50a681e431f07e63623ab3f1e39577'},
    standard.StandardRetryConditions.is_retryable:
        {'4d14d1713bc2806c24b6797b2ec395a29c9b0453'},
    standard.OrRetryChecker.is_retryable: {'5ef0b84b1ef3a49bc193d76a359dbd314682856b'},

    # retries/special.py
    special.RetryDDBChecksumError.is_retryable:
        {'0769cca303874f8dce47dcc93980fa0841fbaab6'},

    # retries/bucket.py
    # See comments in AsyncTokenBucket: we completely replace the TokenBucket
    # implementation from botocore.
    TokenBucket: {'9d543c15de1d582fe99a768fd6d8bde1ed8bb930'},

    # awsresponse.py
    AWSResponse.content: {'1d74998e3e0abe52b52c251a1eae4971e65b1053'},
    AWSResponse.text: {'a724100ba9f6d51b333b8fe470fac46376d5044a'},

    # httpchecksum.py
    handle_checksum_body: {'4b9aeef18d816563624c66c57126d1ffa6fe1993'},
    _handle_bytes_response: {'76f4f9d1da968dc6dbc24fd9f59b4b8ee86799f4'},

    # retryhandler.py
    retryhandler.create_retry_handler: {'fde9dfbc581f3d571f7bf9af1a966f0d28f6d89d'},
    retryhandler.create_checker_from_retry_config:
        {'3022785da77b62e0df06f048da3bb627a2e59bd5'},
    retryhandler._create_single_checker: {'517aaf8efda4bfe851d8dc024513973de1c5ffde'},
    retryhandler._create_single_response_checker:
        {'f55d841e5afa5ebac6b883edf74a9d656415474b'},
    retryhandler.RetryHandler.__call__: {'0ff14b0e97db0d553e8b94a357c11187ca31ea5a'},
    retryhandler.MaxAttemptsDecorator.__call__:
        {'d04ae8ff3ab82940bd7a5ffcd2aa27bf45a4817a'},
    retryhandler.MaxAttemptsDecorator._should_retry:
        {'33af9b4af06372dc2a7985d6cbbf8dfbaee4be2a'},
    retryhandler.MultiChecker.__call__: {'dae2cc32aae9fa0a527630db5c5d8db96d957633'},
    retryhandler.CRC32Checker.__call__: {'4f0b55948e05a9039dc0ba62c80eb341682b85ac'},
    retryhandler.CRC32Checker._check_response:
        {'bc371df204ab7138e792b782e83473e6e9b7a620'},
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

        try:
            source = getsource(obj)
        except TypeError:
            obj = obj.fget
            source = getsource(obj)

        digest = hashlib.sha1(source.encode('utf-8')).hexdigest()

        if digest not in digests:
            print("Digest of {}:{} not found in: {}".format(
                obj.__qualname__, digest, digests))
            success = False

    assert success
