#  BSD 3-Clause License
#
#  Copyright (c) 2019, Elasticsearch BV
#  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  * Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from asyncio import tasks
from concurrent import futures

import pytest

import elasticapm
from elasticapm.conf import constants

pytestmark = [pytest.mark.asyncio]


async def test_async_capture_span(instrument, elasticapm_client):
    @elasticapm.async_capture_span()
    async def do_some_work():
        async with elasticapm.async_capture_span("more-work"):
            await tasks.sleep(0.1)

    elasticapm_client.begin_transaction("test")
    await do_some_work()
    elasticapm_client.end_transaction("test", "OK")
    transaction = elasticapm_client.events[constants.TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 3
    sleep, c, d = spans
    assert sleep["subtype"] == "sleep"
    assert not sleep["sync"]
    assert sleep["transaction_id"] == transaction["id"]

    assert c["type"] == "code"
    assert c["subtype"] == "custom"
    assert not c["sync"]
    assert c["transaction_id"] == transaction["id"]

    assert d["type"] == "code"
    assert d["subtype"] == "custom"
    assert not d["sync"]
    assert d["transaction_id"] == transaction["id"]


async def test_threadpool_executor(instrument, event_loop, elasticapm_client):
    executor = futures.ThreadPoolExecutor(max_workers=2)
    elasticapm_client.begin_transaction("foo")

    def myfunc(a, b):
        with elasticapm.capture_span("nothing"):
            pass

    await event_loop.run_in_executor(executor, myfunc, 1, 2)
    elasticapm_client.end_transaction("bar", "OK")
    transaction = elasticapm_client.events[constants.TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
