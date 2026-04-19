from botocore.retries.special import RetryDDBChecksumError, crc32, logger


class AioRetryDDBChecksumError(RetryDDBChecksumError):
    """Async retry handler for DynamoDB CRC32 checksum errors."""

    async def is_retryable(self, context):
        """Check if the request should be retried due to DynamoDB CRC32 mismatch.

        Args:
            context: The HTTP response context.

        Returns:
            True if the CRC32 checksum failed and request should be retried.
        """
        operation_model = context.operation_model
        if operation_model is None:
            return False
        service_model = operation_model.service_model
        if service_model is None:
            return False
        service_name = service_model.service_name
        if service_name != self._SERVICE_NAME:
            return False
        if context.http_response is None:
            return False
        checksum = context.http_response.headers.get(self._CHECKSUM_HEADER)
        if checksum is None:
            return False
        actual_crc32 = crc32(await context.http_response.content) & 0xFFFFFFFF
        if actual_crc32 != int(checksum):
            logger.debug(
                "DynamoDB crc32 checksum does not match, "
                "expected: %s, actual: %s",
                checksum,
                actual_crc32,
            )
            return True
        return False
