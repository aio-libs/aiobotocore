from botocore.awsrequest import AWSResponse
import botocore.utils


class AioAWSResponse(AWSResponse):
    # Unlike AWSResponse, these return awaitables

    async def _content_prop(self):
        """Content of the response as bytes."""

        if self._content is None:
            # Read the contents.
            # NOTE: requests would attempt to call stream and fall back
            # to a custom generator that would call read in a loop, but
            # we don't rely on this behavior
            self._content = await self.raw.read() or bytes()

        return self._content

    @property
    def content(self):
        return self._content_prop()

    async def _text_prop(self):
        """Content of the response as a proper text type.

        Uses the encoding type provided in the reponse headers to decode the
        response content into a proper text type. If the encoding is not
        present in the headers, UTF-8 is used as a default.
        """
        encoding = botocore.utils.get_encoding_from_headers(self.headers)
        if encoding:
            return (await self.content).decode(encoding)
        else:
            return (await self.content).decode('utf-8')

    @property
    def text(self):
        return self._text_prop()
