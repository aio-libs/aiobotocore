from moto.core.models import BotocoreStubber

AWS_REGION = "us-west-2"
AWS_ACCESS_KEY_ID = "test_AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY = "test_AWS_SECRET_ACCESS_KEY"


def assert_status_code(response, status_code):
    assert response.get("ResponseMetadata", {}).get("HTTPStatusCode") == status_code


def response_success(response):
    return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200


def has_moto_mocks(client, event_name):
    # moto registers mock callbacks with the `before-send` event-name, using
    # specific callbacks for the methods that are generated dynamically. By
    # checking that the first callback is a BotocoreStubber, this verifies
    # that moto mocks are intercepting client requests.
    callbacks = client.meta.events._emitter._lookup_cache[event_name]
    if len(callbacks) > 0:
        stub = callbacks[0]
        assert isinstance(stub, BotocoreStubber)
        return stub.enabled
    return False
