"""Resolution of the optional httpx backend dependency.

aiobotocore's httpx backend prefers httpx2 -- Pydantic's maintained,
API-compatible fork of httpx -- and falls back to the original ``httpx``
package when httpx2 is not installed. The legacy ``httpx`` fallback is
deprecated; ``HttpxSession`` emits a ``DeprecationWarning`` when it is used.
The warning is deferred to backend use (rather than raised here at import)
so aiohttp-only users who merely happen to have ``httpx`` installed are not
warned.

This mirrors the compatibility shim used by authlib's httpx integration. It
is kept free of intra-package imports so that low-level modules (e.g.
``_endpoint_helpers``, which is itself imported by ``httpxsession``) can share
it without creating an import cycle.
"""

try:
    import httpx2 as httpx

    HTTPX_IS_LEGACY = False
except ImportError:
    try:
        import httpx

        HTTPX_IS_LEGACY = True
    except ImportError:
        httpx = None
        HTTPX_IS_LEGACY = False
