from botocore.httpchecksum import (
    _CHECKSUM_CLS,
    FlexibleChecksumError,
    _handle_streaming_response,
    base64,
    logger,
)


async def handle_checksum_body(
    http_response, response, context, operation_model
):
    headers = response["headers"]
    checksum_context = context.get("checksum", {})
    algorithms = checksum_context.get("response_algorithms")

    if not algorithms:
        return

    for algorithm in algorithms:
        header_name = "x-amz-checksum-%s" % algorithm
        # If the header is not found, check the next algorithm
        if header_name not in headers:
            continue

        # If a - is in the checksum this is not valid Base64. S3 returns
        # checksums that include a -# suffix to indicate a checksum derived
        # from the hash of all part checksums. We cannot wrap this response
        if "-" in headers[header_name]:
            continue

        if operation_model.has_streaming_output:
            response["body"] = _handle_streaming_response(
                http_response, response, algorithm
            )
        else:
            response["body"] = await _handle_bytes_response(
                http_response, response, algorithm
            )

        # Expose metadata that the checksum check actually occurred
        checksum_context = response["context"].get("checksum", {})
        checksum_context["response_algorithm"] = algorithm
        response["context"]["checksum"] = checksum_context
        return

    logger.info(
        f'Skipping checksum validation. Response did not contain one of the '
        f'following algorithms: {algorithms}.'
    )


async def _handle_bytes_response(http_response, response, algorithm):
    body = await http_response.content
    header_name = "x-amz-checksum-%s" % algorithm
    checksum_cls = _CHECKSUM_CLS.get(algorithm)
    checksum = checksum_cls()
    checksum.update(body)
    expected = response["headers"][header_name]
    if checksum.digest() != base64.b64decode(expected):
        error_msg = (
            "Expected checksum %s did not match calculated checksum: %s"
            % (
                expected,
                checksum.b64digest(),
            )
        )
        raise FlexibleChecksumError(error_msg=error_msg)
    return body
