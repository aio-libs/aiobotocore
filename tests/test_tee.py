import anyio
import pytest

from aiobotocore._tee import tee


async def _arange(n):
    for i in range(n):
        yield i


async def _collect(iterator, out):
    async for value in iterator:
        out.append(value)


def test_tee_rejects_non_positive_n():
    with pytest.raises(ValueError, match='n must be >= 1'):
        tee(_arange(3), 0)


async def test_tee_fans_out_to_every_consumer():
    a, b, c = tee(_arange(3), 3)

    assert [x async for x in a] == [0, 1, 2]
    assert [x async for x in b] == [0, 1, 2]
    assert [x async for x in c] == [0, 1, 2]


async def test_tee_consumer_drained_before_the_others_start():
    # aioitertools.tee only advances while its first consumer is iterated;
    # here any consumer may drive the source.
    a, b = tee(_arange(3), 2)

    assert [x async for x in b] == [0, 1, 2]
    assert [x async for x in a] == [0, 1, 2]


async def test_tee_interleaved_consumers():
    a, b = tee(_arange(3), 2)
    out_a, out_b = [], []

    async with anyio.create_task_group() as tg:
        tg.start_soon(_collect, a, out_a)
        tg.start_soon(_collect, b, out_b)

    assert out_a == [0, 1, 2]
    assert out_b == [0, 1, 2]


async def test_tee_pulls_each_value_from_the_source_once():
    pulls = 0

    async def counting():
        nonlocal pulls
        for i in range(3):
            pulls += 1
            yield i

    a, b = tee(counting(), 2)

    assert [x async for x in a] == [0, 1, 2]
    assert [x async for x in b] == [0, 1, 2]
    # Values are buffered per consumer, not re-pulled.
    assert pulls == 3


async def test_tee_replays_source_error_to_every_consumer():
    error = RuntimeError('boom')

    async def failing():
        yield 0
        raise error

    a, b = tee(failing(), 2)

    # Buffered values arrive before the error does.
    assert await a.__anext__() == 0
    with pytest.raises(RuntimeError) as excinfo:
        await a.__anext__()
    assert excinfo.value is error

    # The second consumer never touched the source, but sees the same error
    # rather than a silently short stream.
    assert await b.__anext__() == 0
    with pytest.raises(RuntimeError) as excinfo:
        await b.__anext__()
    assert excinfo.value is error


async def test_tee_cancelled_consumer_fails_the_others_loudly():
    async def slow():
        yield 0
        await anyio.sleep(30)  # cancelled here, tearing down the source
        yield 1  # pragma: no cover

    a, b = tee(slow(), 2)
    assert await a.__anext__() == 0
    assert await b.__anext__() == 0

    with anyio.move_on_after(0.5) as scope:
        await a.__anext__()
    assert scope.cancelled_caught

    # The source is dead. Without the poison value b would see
    # StopAsyncIteration and quietly report an empty stream.
    with pytest.raises(RuntimeError, match='tee source was cancelled'):
        await b.__anext__()
