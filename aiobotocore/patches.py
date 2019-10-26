import base64
import asyncio

import botocore.handlers
from botocore.compat import get_md5


async def calculate_md5(params, **kwargs):
    request_dict = params
    if request_dict['body'] and 'Content-MD5' not in params['headers']:
        body = request_dict['body']
        if isinstance(body, (bytes, bytearray)):
            binary_md5 = botocore.handlers._calculate_md5_from_bytes(body)
        else:
            binary_md5, data = await _calculate_md5_from_file(body)

            # aiohttp does not support
            request_dict['body'] = data
        base64_md5 = base64.b64encode(binary_md5).decode('ascii')
        params['headers']['Content-MD5'] = base64_md5


async def conditionally_calculate_md5(params, context, request_signer, **kwargs):
    """Only add a Content-MD5 if the system supports it."""
    if botocore.handlers.MD5_AVAILABLE:
        await calculate_md5(params, **kwargs)


async def _calculate_md5_from_file(fileobj):
    start_position = fileobj.tell()
    md5 = get_md5()
    body = bytes()

    if asyncio.iscoroutinefunction(fileobj.read):
        async for chunk in fileobj.iter_chunks(1024 * 1024):
            body += chunk
            md5.update(chunk)
    else:
        for chunk in iter(lambda: fileobj.read(1024 * 1024), b''):
            md5.update(chunk)

    # fileobj.seek(start_position)
    return md5.digest(), body


_handler_patches = {
    botocore.handlers.conditionally_calculate_md5: conditionally_calculate_md5,
    botocore.handlers.calculate_md5: calculate_md5,
    botocore.handlers._calculate_md5_from_file: _calculate_md5_from_file,
}

_rev_handler_patches = {v: k for k, v in _handler_patches.items()}


def get_handler_patch(method):
    patched_method = _handler_patches.get(method, method)
    return patched_method
