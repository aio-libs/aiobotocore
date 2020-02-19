"""
Test MotoService

Test the aiohttp wrappers on moto.server, which run moto.server in a
thread for each service (batch, s3, etc), using async/await wrappers
to start and stop each server.
"""
import json

import aiohttp
import pytest

from aiobotocore.aiomoto.aiomoto_services import HOST
from aiobotocore.aiomoto.aiomoto_services import MotoService


def test_moto_service():
    # this instantiates a MotoService but does not start a server
    service = MotoService("s3")
    assert HOST in service.endpoint_url
    assert service._server is None


@pytest.mark.asyncio
async def test_moto_batch_service():
    async with MotoService("batch") as batch_service:
        assert batch_service._server  # __aenter__ starts a moto.server

        url = batch_service.endpoint_url + "/v1/describejobqueues"
        batch_query = {"jobQueues": [], "maxResults": 10}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=batch_query, timeout=5) as resp:
                assert resp.status == 200
                job_queues = await resp.text()
                job_queues = json.loads(job_queues)
                assert job_queues["jobQueues"] == []


@pytest.mark.asyncio
async def test_moto_s3_service():
    async with MotoService("s3") as s3_service:
        assert s3_service._server  # __aenter__ starts a moto.server

        url = s3_service.endpoint_url
        s3_xmlns = "http://s3.amazonaws.com/doc/2006-03-01"
        async with aiohttp.ClientSession() as session:
            # https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListBuckets.html
            async with session.get(url, timeout=5) as resp:
                assert resp.status == 200
                content = await resp.text()  # ListAllMyBucketsResult XML
                assert s3_xmlns in content


# This test is not necessary to run every time, but might be useful later.
# @pytest.mark.asyncio
# async def test_moto_api_service():
#     # The moto-api is a flask UI to view moto backends
#     async with MotoService("moto_api") as moto_api_service:
#         assert moto_api_service._server  # __aenter__ starts a moto.server
#
#         url = moto_api_service.endpoint_url + "/moto-api"
#         async with aiohttp.ClientSession() as session:
#             async with session.get(url, timeout=5) as resp:
#                 assert resp.status == 200
#                 content = await resp.text()
#                 assert content
