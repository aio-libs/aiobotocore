from botocore.eventstream import EventStream, EventStreamBuffer


class AioEventStream(EventStream):
    def __init__(self, *args, **kwargs):
        super(AioEventStream, self).__init__(*args, **kwargs)
        self._buffer = EventStreamBuffer()

    def _create_raw_event_generator(self):
        return self._raw_stream.iter_chunks()

    def __iter__(self):
        raise NotImplementedError('Use async-for instead')

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Basically this gets chunks of data from a stream,
        # pumps them into a buffer class that does some error
        # checking, splitting and then returns results
        while True:
            try:
                chunk = await self._event_generator.__anext__()
            except StopAsyncIteration:
                break
            else:
                self._buffer.add_data(chunk[0])

            event = self._get_event_from_buffer()
            if event:
                return event
            # If we have no event, there might be more data needed
            # from the stream, so loop round and try again

        # The stream has been read completely, but the buffer
        # might still have events in it.
        event = self._get_event_from_buffer()
        if event:
            return event

        raise StopAsyncIteration()

    def _get_event_from_buffer(self):
        try:
            while True:
                event = next(self._buffer)
                parsed_event = self._parse_event(event)
                if parsed_event:
                    return parsed_event
        except StopIteration:
            pass

        return None

    async def close(self):
        """Closes the underlying streaming body. """
        await self._raw_stream.close()
