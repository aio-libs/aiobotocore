import datetime
import botocore
import botocore.auth
from botocore.signers import RequestSigner, UnknownSignatureVersionError, \
    UnsupportedSignatureVersionError, create_request_object, prepare_request_dict, \
    _should_use_global_endpoint, S3PostPresigner
from botocore.exceptions import UnknownClientMethodError


class AioRequestSigner(RequestSigner):
    async def handler(self, operation_name=None, request=None, **kwargs):
        # This is typically hooked up to the "request-created" event
        # from a client's event emitter.  When a new request is created
        # this method is invoked to sign the request.
        # Don't call this method directly.
        return await self.sign(operation_name, request)

    async def sign(self, operation_name, request, region_name=None,
                   signing_type='standard', expires_in=None,
                   signing_name=None):
        explicit_region_name = region_name
        if region_name is None:
            region_name = self._region_name

        if signing_name is None:
            signing_name = self._signing_name

        signature_version = await self._choose_signer(
            operation_name, signing_type, request.context)

        # Allow mutating request before signing
        await self._event_emitter.emit(
            'before-sign.{0}.{1}'.format(
                self._service_id.hyphenize(), operation_name),
            request=request, signing_name=signing_name,
            region_name=self._region_name,
            signature_version=signature_version, request_signer=self,
            operation_name=operation_name
        )

        if signature_version != botocore.UNSIGNED:
            kwargs = {
                'signing_name': signing_name,
                'region_name': region_name,
                'signature_version': signature_version
            }
            if expires_in is not None:
                kwargs['expires'] = expires_in
            if not explicit_region_name and request.context.get(
                    'signing', {}).get('region'):
                kwargs['region_name'] = request.context['signing']['region']
            try:
                auth = await self.get_auth_instance(**kwargs)
            except UnknownSignatureVersionError as e:
                if signing_type != 'standard':
                    raise UnsupportedSignatureVersionError(
                        signature_version=signature_version)
                else:
                    raise e

            auth.add_auth(request)

    async def get_auth_instance(self, signing_name, region_name,
                                signature_version=None, **kwargs):
        if signature_version is None:
            signature_version = self._signature_version

        cls = botocore.auth.AUTH_TYPE_MAPS.get(signature_version)
        if cls is None:
            raise UnknownSignatureVersionError(
                signature_version=signature_version)

        frozen_credentials = None
        if self._credentials is not None:
            frozen_credentials = await self._credentials.get_frozen_credentials()
        kwargs['credentials'] = frozen_credentials
        if cls.REQUIRES_REGION:
            if self._region_name is None:
                raise botocore.exceptions.NoRegionError()
            kwargs['region_name'] = region_name
            kwargs['service_name'] = signing_name
        auth = cls(**kwargs)
        return auth

    # Alias get_auth for backwards compatibility.
    get_auth = get_auth_instance

    async def _choose_signer(self, operation_name, signing_type, context):
        signing_type_suffix_map = {
            'presign-post': '-presign-post',
            'presign-url': '-query'
        }
        suffix = signing_type_suffix_map.get(signing_type, '')

        signature_version = self._signature_version
        if signature_version is not botocore.UNSIGNED and not \
                signature_version.endswith(suffix):
            signature_version += suffix

        handler, response = await self._event_emitter.emit_until_response(
            'choose-signer.{0}.{1}'.format(
                self._service_id.hyphenize(), operation_name),
            signing_name=self._signing_name, region_name=self._region_name,
            signature_version=signature_version, context=context)

        if response is not None:
            signature_version = response
            # The suffix needs to be checked again in case we get an improper
            # signature version from choose-signer.
            if signature_version is not botocore.UNSIGNED and not \
                    signature_version.endswith(suffix):
                signature_version += suffix

        return signature_version

    async def generate_presigned_url(self, request_dict, operation_name,
                                     expires_in=3600, region_name=None,
                                     signing_name=None):
        request = create_request_object(request_dict)
        await self.sign(operation_name, request, region_name,
                        'presign-url', expires_in, signing_name)

        request.prepare()
        return request.url


def add_generate_presigned_url(class_attributes, **kwargs):
    class_attributes['generate_presigned_url'] = generate_presigned_url


async def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=3600,
                                 HttpMethod=None):
    """Generate a presigned url given a client, its method, and arguments

    :type ClientMethod: string
    :param ClientMethod: The client method to presign for

    :type Params: dict
    :param Params: The parameters normally passed to
        ``ClientMethod``.

    :type ExpiresIn: int
    :param ExpiresIn: The number of seconds the presigned url is valid
        for. By default it expires in an hour (3600 seconds)

    :type HttpMethod: string
    :param HttpMethod: The http method to use on the generated url. By
        default, the http method is whatever is used in the method's model.

    :returns: The presigned url
    """
    client_method = ClientMethod
    params = Params
    if params is None:
        params = {}
    expires_in = ExpiresIn
    http_method = HttpMethod
    context = {
        'is_presign_request': True,
        'use_global_endpoint': _should_use_global_endpoint(self),
    }

    request_signer = self._request_signer
    serializer = self._serializer

    try:
        operation_name = self._PY_TO_OP_NAME[client_method]
    except KeyError:
        raise UnknownClientMethodError(method_name=client_method)

    operation_model = self.meta.service_model.operation_model(
        operation_name)

    params = await self._emit_api_params(params, operation_model, context)

    # Create a request dict based on the params to serialize.
    request_dict = serializer.serialize_to_request(
        params, operation_model)

    # Switch out the http method if user specified it.
    if http_method is not None:
        request_dict['method'] = http_method

    # Prepare the request dict by including the client's endpoint url.
    prepare_request_dict(
        request_dict, endpoint_url=self.meta.endpoint_url, context=context)

    # Generate the presigned url.
    return await request_signer.generate_presigned_url(
        request_dict=request_dict, expires_in=expires_in,
        operation_name=operation_name)


class AioS3PostPresigner(S3PostPresigner):
    async def generate_presigned_post(self, request_dict, fields=None,
                                      conditions=None, expires_in=3600,
                                      region_name=None):
        if fields is None:
            fields = {}

        if conditions is None:
            conditions = []

        # Create the policy for the post.
        policy = {}

        # Create an expiration date for the policy
        datetime_now = datetime.datetime.utcnow()
        expire_date = datetime_now + datetime.timedelta(seconds=expires_in)
        policy['expiration'] = expire_date.strftime(botocore.auth.ISO8601)

        # Append all of the conditions that the user supplied.
        policy['conditions'] = []
        for condition in conditions:
            policy['conditions'].append(condition)

        # Store the policy and the fields in the request for signing
        request = create_request_object(request_dict)
        request.context['s3-presign-post-fields'] = fields
        request.context['s3-presign-post-policy'] = policy

        await self._request_signer.sign(
            'PutObject', request, region_name, 'presign-post')
        # Return the url and the fields for th form to post.
        return {'url': request.url, 'fields': fields}


def add_generate_presigned_post(class_attributes, **kwargs):
    class_attributes['generate_presigned_post'] = generate_presigned_post


async def generate_presigned_post(self, Bucket, Key, Fields=None, Conditions=None,
                                  ExpiresIn=3600):
    bucket = Bucket
    key = Key
    fields = Fields
    conditions = Conditions
    expires_in = ExpiresIn

    if fields is None:
        fields = {}

    if conditions is None:
        conditions = []

    post_presigner = AioS3PostPresigner(self._request_signer)
    serializer = self._serializer

    # We choose the CreateBucket operation model because its url gets
    # serialized to what a presign post requires.
    operation_model = self.meta.service_model.operation_model(
        'CreateBucket')

    # Create a request dict based on the params to serialize.
    request_dict = serializer.serialize_to_request(
        {'Bucket': bucket}, operation_model)

    # Prepare the request dict by including the client's endpoint url.
    prepare_request_dict(
        request_dict, endpoint_url=self.meta.endpoint_url,
        context={
            'is_presign_request': True,
            'use_global_endpoint': _should_use_global_endpoint(self),
        },
    )

    # Append that the bucket name to the list of conditions.
    conditions.append({'bucket': bucket})

    # If the key ends with filename, the only constraint that can be
    # imposed is if it starts with the specified prefix.
    if key.endswith('${filename}'):
        conditions.append(["starts-with", '$key', key[:-len('${filename}')]])
    else:
        conditions.append({'key': key})

    # Add the key to the fields.
    fields['key'] = key

    return await post_presigner.generate_presigned_post(
        request_dict=request_dict, fields=fields, conditions=conditions,
        expires_in=expires_in)
