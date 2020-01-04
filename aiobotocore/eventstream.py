from botocore.eventstream import EventStream, EventStreamBuffer
from async_generator import async_generator, yield_


class AioEventStream(EventStream):
    @async_generator
    async def _create_raw_event_generator(self):
        event_stream_buffer = EventStreamBuffer()
        async for chunk, _ in self._raw_stream.iter_chunks():
            event_stream_buffer.add_data(chunk)
            for event in event_stream_buffer:
                await yield_(event)

    def __iter__(self):
        raise NotImplementedError('Use async-for instead')

    def __aiter__(self):
        return self.__anext__()

    @async_generator
    async def __anext__(self):
        async for event in self._event_generator:
            parsed_event = self._parse_event(event)
            if parsed_event:
                await yield_(parsed_event)
