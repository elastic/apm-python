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

sanic = pytest.importorskip("sanic")  # isort:skip

from elasticapm.conf import constants

pytestmark = [pytest.mark.sanic]  # isort:skip


@pytest.mark.parametrize(
    "url, transaction_name, span_count, custom_context",
    [("/", "GET /", 1, {}), ("/greet/sanic", "GET /greet/<name:string>", 0, {"name": "sanic"})],
)
def test_get(url, transaction_name, span_count, custom_context, sanic_elastic_app, elasticapm_client):
    sanic_app, apm = next(sanic_elastic_app(elastic_client=elasticapm_client))
    source_request, response = sanic_app.test_client.get(
        url,
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
        },
    )  # type: sanic.response.HttpResponse

    assert len(elasticapm_client.events[constants.TRANSACTION]) == 1
    transaction = elasticapm_client.events[constants.TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == span_count
    if span_count > 0:
        span = spans[0]
        assert span["name"] == "test"
        assert transaction["span_count"]["started"] == span_count

    for field, value in {
        "name": transaction_name,
        "result": "HTTP 2xx",
        "outcome": "success",
        "type": "request",
    }.items():
        assert transaction[field] == value

    request = transaction["context"]["request"]
    assert request["method"] == "GET"
    assert request["socket"] == {"remote_address": f"127.0.0.1", "encrypted": False}
    context = transaction["context"]["custom"]
    assert context == custom_context


def test_capture_exception(sanic_elastic_app, elasticapm_client):
    sanic_app, apm = next(sanic_elastic_app(elastic_client=elasticapm_client))
    _, _ = sanic_app.test_client.get(
        "/capture-exception",
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
        },
    )

    assert len(elasticapm_client.events[constants.ERROR]) == 1
    transaction = elasticapm_client.events[constants.TRANSACTION][0]

    for field, value in {
        "name": "GET /capture-exception",
        "result": "HTTP 5xx",
        "outcome": "failure",
        "type": "request",
    }.items():
        assert transaction[field] == value


def test_unhandled_exception_capture(sanic_elastic_app, elasticapm_client):
    sanic_app, apm = next(sanic_elastic_app(elastic_client=elasticapm_client))
    _, resp = sanic_app.test_client.post(
        "/v1/apm/unhandled-exception",
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
        },
    )
    assert len(elasticapm_client.events[constants.ERROR]) == 1
    transaction = elasticapm_client.events[constants.TRANSACTION][0]
    for field, value in {
        "name": "POST /v1/apm/unhandled-exception",
        "result": "HTTP 5xx",
        "outcome": "failure",
        "type": "request",
    }.items():
        assert transaction[field] == value


@pytest.mark.parametrize(
    "url, expected_source",
    [
        ("/raise-exception", "custom-handler"),
        ("/raise-value-error", "value-error-custom"),
        ("/fallback-value-error", "custom-handler-default"),
    ],
)
def test_client_with_custom_error_handler(
    url, expected_source, sanic_elastic_app, elasticapm_client, custom_error_handler
):
    sanic_app, apm = next(sanic_elastic_app(elastic_client=elasticapm_client, error_handler=custom_error_handler))
    _, resp = sanic_app.test_client.get(
        url,
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
        },
    )
    assert len(elasticapm_client.events[constants.ERROR]) == 1
    assert resp.json["source"] == expected_source


def test_header_field_sanitization(sanic_elastic_app, elasticapm_client):
    sanic_app, apm = next(sanic_elastic_app(elastic_client=elasticapm_client))
    _, resp = sanic_app.test_client.get(
        "/add-custom-headers",
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
            "API_KEY": "some-random-api-key",
        },
    )
    assert len(apm._client.events[constants.TRANSACTION]) == 1
    transaction = apm._client.events[constants.TRANSACTION][0]
    assert transaction["context"]["response"]["headers"]["sessionid"] == "[REDACTED]"
    assert transaction["context"]["request"]["headers"]["api_key"] == "[REDACTED]"


def test_custom_callback_handlers(sanic_elastic_app, elasticapm_client):
    def _custom_transaction_callback(request):
        return "my-custom-name"

    async def _user_info_callback(request):
        return "test", "test@test.com", 1234356

    async def _label_callback(request):
        return {
            "label1": "value1",
            "label2": 19,
        }

    sanic_app, apm = next(
        sanic_elastic_app(
            elastic_client=elasticapm_client,
            transaction_name_callback=_custom_transaction_callback,
            user_context_callback=_user_info_callback,
            label_info_callback=_label_callback,
        )
    )
    _, resp = sanic_app.test_client.get(
        "/add-custom-headers",
        headers={
            constants.TRACEPARENT_HEADER_NAME: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03",
            constants.TRACESTATE_HEADER_NAME: "foo=bar,bar=baz",
            "REMOTE_ADDR": "127.0.0.1",
            "API_KEY": "some-random-api-key",
        },
    )
    assert len(apm._client.events[constants.TRANSACTION]) == 1
    assert apm._client.events[constants.TRANSACTION][0]["name"] == "my-custom-name"
    assert apm._client.events[constants.TRANSACTION][0]["context"]["user"]["username"] == "test"
    assert apm._client.events[constants.TRANSACTION][0]["context"]["user"]["id"] == 1234356
    assert apm._client.events[constants.TRANSACTION][0]["context"]["tags"]["label2"] == 19
