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


_patches = {
    botocore.handlers.conditionally_calculate_md5: conditionally_calculate_md5,
    botocore.handlers.calculate_md5: calculate_md5,
    botocore.handlers._calculate_md5_from_file: _calculate_md5_from_file,
}

_rev_patches = {v: k for k, v in _patches.items()}


def patch():
    updated = False

    for method, patched_method in _patches.items():
        if method != patched_method:
            method.__globals__[method.__name__] = patched_method
            updated = True

    if updated:
        for idx, (handler_name, handler_method, *_) in enumerate(botocore.handlers.BUILTIN_HANDLERS):
            patched_method = _patches.get(handler_method)
            if patched_method:
                botocore.handlers.BUILTIN_HANDLERS[idx] = (handler_name, patched_method)


def unpatch():
    updated = False

    for method, patched_method in _patches.items():
        if method == patched_method:
            method.__globals__[method.__name__] = method
            updated = True

    if updated:
        for idx, (handler_name, handler_method) in enumerate(botocore.handlers.BUILTIN_HANDLERS):
            method = _rev_patches.get(handler_method)
            if method:
                botocore.handlers.BUILTIN_HANDLERS[idx] = (handler_name, method)
