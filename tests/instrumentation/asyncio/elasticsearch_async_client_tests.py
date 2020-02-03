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

import pytest  # isort:skip

pytest.importorskip("elasticsearch_async")  # isort:skip

import os

from elasticsearch import VERSION as ES_VERSION

from elasticapm.conf.constants import TRANSACTION
from elasticsearch_async import AsyncElasticsearch

pytestmark = [pytest.mark.elasticsearch, pytest.mark.asyncio, pytest.mark.integrationtest]

if "ES_URL" not in os.environ:
    pytestmark.append(pytest.mark.skip("Skipping elasticsearch test, no ES_URL environment variable"))


document_type = "_doc" if ES_VERSION[0] >= 6 else "doc"


@pytest.fixture
async def elasticsearch_async(request):
    """Elasticsearch client fixture."""
    client = AsyncElasticsearch(hosts=os.environ["ES_URL"])
    try:
        yield client
    finally:
        await client.indices.delete(index="*")
        await client.transport.close()


async def test_ping(instrument, elasticapm_client, elasticsearch_async):
    elasticapm_client.begin_transaction("test")
    result = await elasticsearch_async.ping()
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES HEAD /"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["sync"] is False


async def test_info(instrument, elasticapm_client, elasticsearch_async):
    elasticapm_client.begin_transaction("test")
    result = await elasticsearch_async.info()
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES GET /"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["sync"] is False


async def test_create(instrument, elasticapm_client, elasticsearch_async):
    elasticapm_client.begin_transaction("test")
    if ES_VERSION[0] < 5:
        r1 = await elasticsearch_async.create("tweets", document_type, {"user": "kimchy", "text": "hola"}, 1)
    elif ES_VERSION[0] < 7:
        r1 = await elasticsearch_async.create("tweets", document_type, 1, body={"user": "kimchy", "text": "hola"})
    else:
        r1 = await elasticsearch_async.create("tweets", 1, body={"user": "kimchy", "text": "hola"})
    r2 = await elasticsearch_async.create(
        index="tweets", doc_type=document_type, id=2, body={"user": "kimchy", "text": "hola"}, refresh=True
    )
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for i, span in enumerate(spans):
        if ES_VERSION[0] >= 5:
            assert span["name"] == "ES PUT /tweets/%s/%d/_create" % (document_type, i + 1)
        else:
            assert span["name"] == "ES PUT /tweets/%s/%d" % (document_type, i + 1)
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]


async def test_search_body(instrument, elasticapm_client, elasticsearch_async):
    await elasticsearch_async.create(
        index="tweets", doc_type=document_type, id=1, body={"user": "kimchy", "text": "hola"}, refresh=True
    )
    elasticapm_client.begin_transaction("test")
    search_query = {"query": {"term": {"user": "kimchy"}}}
    result = await elasticsearch_async.search(body=search_query, params=None)
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["hits"]["hits"][0]["_source"] == {"user": "kimchy", "text": "hola"}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] in ("ES GET /_search", "ES GET /_all/_search")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"] == '{"term": {"user": "kimchy"}}'
    assert span["sync"] is False


async def test_count_body(instrument, elasticapm_client, elasticsearch_async):
    await elasticsearch_async.create(
        index="tweets", doc_type=document_type, id=1, body={"user": "kimchy", "text": "hola"}, refresh=True
    )
    elasticapm_client.begin_transaction("test")
    search_query = {"query": {"term": {"user": "kimchy"}}}
    result = await elasticsearch_async.count(body=search_query)
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["count"] == 1
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] in ("ES GET /_count", "ES POST /_count", "ES GET /_all/_count")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"] == '{"term": {"user": "kimchy"}}'
    assert span["sync"] is False


@pytest.mark.skipif(ES_VERSION[0] < 5, reason="unsupported method")
async def test_delete_by_query_body(instrument, elasticapm_client, elasticsearch_async):
    await elasticsearch_async.create(
        index="tweets", doc_type=document_type, id=1, body={"user": "kimchy", "text": "hola"}, refresh=True
    )
    elasticapm_client.begin_transaction("test")
    result = await elasticsearch_async.delete_by_query(index="tweets", body={"query": {"term": {"user": "kimchy"}}})
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)

    span = spans[0]
    assert span["name"] == "ES POST /tweets/_delete_by_query"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"] == '{"term": {"user": "kimchy"}}'
    assert span["sync"] is False
