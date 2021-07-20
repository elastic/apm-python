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

import base64
import functools
import json
import os

import elasticapm
from elasticapm.base import Client, get_client
from elasticapm.conf import constants
from elasticapm.utils import compat, encoding, get_name_from_func
from elasticapm.utils.disttracing import TraceParent
from elasticapm.utils.logging import get_logger

logger = get_logger("elasticapm.serverless")

COLD_START = True


class capture_serverless(object):
    """
    Context manager and decorator designed for instrumenting serverless
    functions.

    Begins and ends a single transaction, waiting for the transport to flush
    before returning from the wrapped function
    """

    def __init__(self, name=None, **kwargs):
        self.name = name
        self.event = {}
        self.context = {}
        self.response = None

        # Disable all background threads except for transport
        kwargs["disable_metrics_thread"] = True
        kwargs["central_config"] = False
        kwargs["cloud_provider"] = "none"

        if "framework_name" not in kwargs:
            kwargs["framework_name"] = os.environ.get("AWS_EXECUTION_ENV", "AWS_Lambda_python")

        self.client = get_client()
        if not self.client:
            self.client = Client(**kwargs)
        if not self.client.config.debug and self.client.config.instrument and self.client.config.enabled:
            elasticapm.instrument()

    def __call__(self, func):
        self.name = self.name or get_name_from_func(func)

        @functools.wraps(func)
        def decorated(*args, **kwds):
            if len(args) == 2:
                # Saving these for request context later
                self.event, self.context = args
            else:
                self.event, self.context = {}, {}
            if not self.client.config.debug and self.client.config.instrument and self.client.config.enabled:
                with self:
                    with elasticapm.capture_span(self.name):
                        self.response = func(*args, **kwds)
                    return self.response
            else:
                return func(*args, **kwds)

        return decorated

    def __enter__(self):
        """
        Transaction setup
        """
        trace_parent = TraceParent.from_headers(self.event.get("headers", {}))
        if "httpMethod" in self.event:
            global COLD_START
            if COLD_START:
                # TODO
                COLD_START = False
            self.start_time = self.event.get("requestContext", {}).get("requestTimeEpoch")
            if self.start_time:
                self.start_time = float(self.start_time) * 0.001
                self.transaction = self.client.begin_transaction(
                    "request", trace_parent=trace_parent, start=self.start_time
                )
            else:
                self.transaction = self.client.begin_transaction("request", trace_parent=trace_parent)
            elasticapm.set_context(
                lambda: get_data_from_request(
                    self.event,
                    capture_body=self.client.config.capture_body in ("transactions", "all"),
                    capture_headers=self.client.config.capture_headers,
                ),
                "request",
            )
            if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
                elasticapm.set_transaction_name(
                    "{} {}".format(self.event["httpMethod"], os.environ["AWS_LAMBDA_FUNCTION_NAME"])
                )
            else:
                elasticapm.set_transaction_name(self.name, override=False)
        else:
            self.transaction = self.client.begin_transaction("function", trace_parent=trace_parent)
            elasticapm.set_transaction_name(os.environ.get("AWS_LAMBDA_FUNCTION_NAME", self.name), override=False)

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Transaction teardown
        """
        if exc_val:
            self.client.capture_exception(exc_info=(exc_type, exc_val, exc_tb), handled=False)

        if self.response and isinstance(self.response, dict):
            elasticapm.set_context(
                lambda: get_data_from_response(self.response, capture_headers=self.client.config.capture_headers),
                "response",
            )
            if "statusCode" in self.response:
                result = "HTTP {}xx".format(int(self.response["statusCode"]) // 100)
                elasticapm.set_transaction_result(result, override=False)

        self.client.end_transaction()

        try:
            self.client._transport.flush()
        except ValueError:
            logger.warning("flush timed out")


def get_data_from_request(event, capture_body=False, capture_headers=True):
    """
    Capture context data from API gateway event
    """
    result = {}
    if capture_headers and "headers" in event:
        result["headers"] = event["headers"]
    if "httpMethod" not in event:
        # Not API Gateway
        return result

    result["method"] = event["httpMethod"]
    if event["httpMethod"] in constants.HTTP_WITH_BODY and "body" in event:
        body = event["body"]
        if capture_body:
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body)
            else:
                try:
                    jsonbody = json.loads(body)
                    body = jsonbody
                except Exception:
                    pass

        if body is not None:
            result["body"] = body if capture_body else "[REDACTED]"

    result["url"] = get_url_dict(event)
    return result


def get_data_from_response(response, capture_headers=True):
    """
    Capture response data from lambda return
    """
    result = {}

    if "statusCode" in response:
        result["status_code"] = response["statusCode"]

    if capture_headers and "headers" in response:
        result["headers"] = response["headers"]
    return result


def get_url_dict(event):
    """
    Reconstruct URL from API Gateway
    """
    headers = event.get("headers", {})
    proto = headers.get("X-Forwarded-Proto", "https")
    host = headers.get("Host", "")
    path = event.get("path", "")
    port = headers.get("X-Forwarded-Port")
    stage = "/" + event.get("requestContext", {}).get("stage", "")
    query = ""
    if event.get("queryStringParameters"):
        query = "?"
        for k, v in compat.iteritems(event["queryStringParameters"]):
            query += "{}={}".format(k, v)
    url = proto + "://" + host + stage + path + query

    url_dict = {
        "full": encoding.keyword_field(url),
        "protocol": proto,
        "hostname": encoding.keyword_field(host),
        "pathname": encoding.keyword_field(stage + path),
    }

    if port:
        url_dict["port"] = port
    if query:
        url_dict["search"] = encoding.keyword_field(query)
    return url_dict
