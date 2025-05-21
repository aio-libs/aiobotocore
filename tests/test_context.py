from botocore.context import get_context

from aiobotocore.context import with_current_context


async def test_with_current_context():
    @with_current_context()
    async def do_something():
        ctx = get_context()
        return ctx is not None

    assert (await do_something()) is True


async def test_with_current_context_with_hook():
    def register_fake():
        ctx = get_context()
        ctx.features.add('FOO')

    @with_current_context(register_fake)
    async def do_something():
        ctx = get_context()
        return ctx.features == {'FOO'}

    assert (await do_something()) is True
