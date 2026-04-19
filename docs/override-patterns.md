# aiobotocore Override Patterns

## Architecture

aiobotocore never monkey-patches botocore. It subclasses botocore
classes, overrides methods with async versions, and wires the async
classes in at session creation time via component registration.

## Pattern 1: Subclass + async override

The most common pattern. Subclass with `Aio` prefix, override the
method as `async def`, await I/O calls.

```python
# aiobotocore/client.py
class AioBaseClient(BaseClient):
    async def _make_api_call(self, operation_name, api_params):
        # Same logic as BaseClient._make_api_call but with:
        # - await on endpoint._send_request()
        # - await on event hooks
        ...
```

Files using this: `client.py`, `endpoint.py`, `credentials.py`,
`session.py`, `args.py`, `signers.py`, `paginate.py`, `waiter.py`.

## Pattern 2: Component registration at session init

`AioSession.__init__()` calls parent then replaces components:

```python
# aiobotocore/session.py:87
class AioSession(Session):
    def _register_response_parser_factory(self):
        self._components.register_component(
            'response_parser_factory',
            AioResponseParserFactory()
        )
```

This ensures all clients created from the session use async
components. The registration happens for: response parsers,
credential resolvers, smart defaults factory.

## Pattern 3: HTTP session replacement

botocore uses urllib3 (sync). aiobotocore replaces the entire
HTTP layer:

- `AioEndpoint.__init__()` requires an `http_session` parameter
  (aiobotocore/endpoint.py:68)
- `AioEndpoint._send()` calls `await self.http_session.send()`
- `AIOHTTPSession` (aiobotocore/httpsession.py) wraps aiohttp
- `HttpxSession` (aiobotocore/httpxsession.py) wraps httpx
- `AioConfig.http_session_cls` selects which HTTP backend to use

The HTTP session is created during `AioEndpointCreator.create_endpoint()`
and managed as an async context manager by `AioBaseClient`.

## Pattern 4: Credential async layer

Credential providers/fetchers that do network I/O (IMDS, STS,
SSO, container) need async versions:

```python
# aiobotocore/credentials.py
class AioRefreshableCredentials(RefreshableCredentials):
    def __init__(self, ...):
        ...
        self._refresh_lock = asyncio.Lock()  # replaces threading.Lock

    async def _protected_refresh(self, is_mandatory):
        async with self._refresh_lock:
            ...
```

Key async credential classes: `AioRefreshableCredentials`,
`AioDeferredRefreshableCredentials`, `AioCachedCredentialFetcher`,
`AioAssumeRoleCredentialFetcher`, `AioSSOCredentialFetcher`,
`AioInstanceMetadataProvider`, `AioContainerProvider`,
`AioLoginCredentialFetcher`.

The credential resolver chain is built in
`aiobotocore/credentials.py:create_credential_resolver()` which
mirrors botocore's but uses Aio providers.

## Pattern 5: resolve_awaitable

```python
# aiobotocore/_helpers.py
async def resolve_awaitable(obj):
    if inspect.isawaitable(obj):
        return await obj
    return obj
```

Used when code handles mixed sync/async callbacks — primarily in
the event hook system (`AioHierarchicalEmitter._emit()` in
hooks.py:68). botocore's built-in handlers are sync, but
aiobotocore overrides may be async. `resolve_awaitable` lets the
emitter handle both transparently.

## Pattern 6: Event hook system

`AioHierarchicalEmitter` (hooks.py:48) subclasses botocore's
emitter. Its `_emit()` method awaits each handler response via
`resolve_awaitable()`. This means:

- Stock botocore handlers (sync) work unchanged
- aiobotocore can register async handlers
- No need to wrap every botocore handler

## Pattern 7: AioConfig

`AioConfig(botocore.client.Config)` (config.py:42) adds:

- `connector_args`: aiohttp connector tuning (ssl, keepalive, etc.)
- `http_session_cls`: pluggable HTTP backend class

Custom merge logic preserves these fields when configs are combined.

## Pattern 8: Async context managers

Clients must be used as async context managers:

```python
async with session.create_client('s3') as client:
    await client.get_object(...)
```

`AioSession.create_client()` returns a `ClientCreatorContext`
(session.py:129) which is an async context manager. On exit,
it closes the HTTP session. This is enforced —
`AioBaseClient.__aexit__()` closes the HTTP session and
`AioBaseClient.__del__()` warns if the client wasn't properly
closed.

## Test porting pattern

Tests in `tests/botocore_tests/` are adapted from botocore's
test suite:

1. Copy the relevant test from botocore's `tests/unit/` or
   `tests/functional/`
2. Replace `botocore.session.Session` with `AioSession`
3. Replace `botocore.stub.Stubber` usage with
   `tests/botocore_tests/helpers.py:StubbedSession`
4. Convert test functions to `async def test_*()`
   (no `@pytest.mark.asyncio` needed — `asyncio_mode = "auto"`)
5. Add `await` to client calls and context managers
6. Replace `unittest.mock.patch` with async-compatible mocking
   where needed
7. Use `AioAWSResponse` instead of `AWSResponse` for mocked
   responses
8. Add docstring: "Taken from [botocore URL] and adapted for
   asyncio and pytest"

See `tests/botocore_tests/unit/test_credentials.py` for a
comprehensive example.
