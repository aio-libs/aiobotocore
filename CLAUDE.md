# Repository

Default branch is `main` (not `master`). Use `--base main` for PRs.

# Pre-commit checks

Run before committing:

```
uv run pre-commit run --all --show-diff-on-failure
```

Key constraints:
- YAML lines must be ≤120 chars (`# yamllint disable-line rule:line-length` for exceptions)
- All workflow jobs must have `timeout-minutes`
- Python code formatted with `ruff`

# Tests

```
uv run make mototest    # moto-based tests (CI runs these)
uv run pytest -sv tests/test_patches.py  # hash validation
```

# Botocore version updates

See `CONTRIBUTING.rst` "How to Upgrade Botocore" section. Key files:
- `pyproject.toml` — botocore version range
- `aiobotocore/__init__.py` — aiobotocore version
- `tests/test_patches.py` — SHA1 hashes of patched botocore code
- `CHANGES.rst` — changelog

# How aiobotocore overrides botocore

aiobotocore makes botocore async by **subclassing** (never monkey-patching). Each override follows the same pattern: subclass with `Aio` prefix, override sync methods with `async def`, call `await` on I/O operations.

See [docs/override-patterns.md](docs/override-patterns.md) for the full reference of patterns, wiring, and test porting.

## Quick reference

**Override chain** (how a client call flows):
`AioSession.create_client()` → `AioClientCreator.create_client()` → `AioBaseClient._make_api_call()` → `AioEndpoint._send_request()` → `AIOHTTPSession.send()`

**Key files and what they override:**

| aiobotocore file | botocore equivalent | What it does |
|-|-|-|
| session.py | session.py | Registers async components, returns async client context |
| client.py | client.py | Async client creation and API calls |
| endpoint.py | endpoint.py | Async HTTP request/response |
| httpsession.py | httpsession.py | aiohttp-based HTTP (replaces urllib3) |
| credentials.py | credentials.py | Async credential refresh with asyncio.Lock |
| hooks.py | hooks.py | Event emitter that awaits async handlers |
| args.py | args.py | Wires AioEndpointCreator + AioConfig |
| config.py | config.py | Adds connector_args, http_session_cls |
| _helpers.py | (none) | `resolve_awaitable()` — awaits if coroutine, returns if not |
