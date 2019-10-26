import botocore
from botocore.signers import RequestSigner, UnknownSignatureVersionError, \
    UnsupportedSignatureVersionError, create_request_object


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

            try:
                auth = self.get_auth_instance(**kwargs)
            except UnknownSignatureVersionError as e:
                if signing_type != 'standard':
                    raise UnsupportedSignatureVersionError(
                        signature_version=signature_version)
                else:
                    raise e

            auth.add_auth(request)

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
