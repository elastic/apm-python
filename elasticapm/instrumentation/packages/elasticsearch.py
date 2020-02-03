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

from __future__ import absolute_import

import json

import elasticapm
from elasticapm.instrumentation.packages.base import AbstractInstrumentedModule
from elasticapm.utils import compat
from elasticapm.utils.logging import get_logger

logger = get_logger("elasticapm.instrument")


API_METHOD_KEY_NAME = "__elastic_apm_api_method_name"
BODY_REF_NAME = "__elastic_apm_body_ref"


class ElasticSearchConnectionMixin(object):
    query_methods = ("Elasticsearch.search", "Elasticsearch.count", "Elasticsearch.delete_by_query")

    def get_signature(self, args, kwargs):
        args_len = len(args)
        http_method = args[0] if args_len else kwargs.get("method")
        http_path = args[1] if args_len > 1 else kwargs.get("url")

        return "ES %s %s" % (http_method, http_path)

    def get_context(self, args, kwargs):
        args_len = len(args)
        params = args[2] if args_len > 2 else kwargs.get("params")
        body = params.pop(BODY_REF_NAME, None) if params else None

        api_method = params.pop(API_METHOD_KEY_NAME, None) if params else None
        context = {"db": {"type": "elasticsearch"}}
        if api_method in self.query_methods:
            query = []
            # using both q AND body is allowed in some API endpoints / ES versions,
            # but not in others. We simply capture both if they are there so the
            # user can see it.
            if params and "q" in params:
                # 'q' is already encoded to a byte string at this point
                # we assume utf8, which is the default
                query.append("q=" + params["q"].decode("utf-8", errors="replace"))
            if isinstance(body, dict) and "query" in body:
                query.append(json.dumps(body["query"], default=compat.text_type))
            if query:
                context["db"]["statement"] = "\n\n".join(query)
        elif api_method == "Elasticsearch.update":
            if isinstance(body, dict) and "script" in body:
                # only get the `script` field from the body
                context["db"]["statement"] = json.dumps({"script": body["script"]})
        # TODO: add instance.base_url to context once we agreed on a format
        return context


class ElasticsearchConnectionInstrumentation(ElasticSearchConnectionMixin, AbstractInstrumentedModule):
    name = "elasticsearch_connection"

    instrument_list = [
        ("elasticsearch.connection.http_urllib3", "Urllib3HttpConnection.perform_request"),
        ("elasticsearch.connection.http_requests", "RequestsHttpConnection.perform_request"),
    ]

    def call(self, module, method, wrapped, instance, args, kwargs):
        signature = self.get_signature(args, kwargs)
        context = self.get_context(args, kwargs)

        with elasticapm.capture_span(
            signature,
            span_type="db",
            span_subtype="elasticsearch",
            span_action="query",
            extra=context,
            skip_frames=2,
            leaf=True,
        ):
            return wrapped(*args, **kwargs)


class ElasticsearchInstrumentation(AbstractInstrumentedModule):
    name = "elasticsearch"

    instrument_list = [
        ("elasticsearch.client", "Elasticsearch.delete_by_query"),
        ("elasticsearch.client", "Elasticsearch.search"),
        ("elasticsearch.client", "Elasticsearch.count"),
        ("elasticsearch.client", "Elasticsearch.update"),
    ]

    def __init__(self):
        super(ElasticsearchInstrumentation, self).__init__()
        try:
            from elasticsearch import VERSION

            self.version = VERSION[0]
        except ImportError:
            self.version = None

    def instrument(self):
        if self.version and not 2 <= self.version < 8:
            logger.debug("Instrumenting version %s of Elasticsearch is not supported by Elastic APM", self.version)
            return
        super(ElasticsearchInstrumentation, self).instrument()

    def call(self, module, method, wrapped, instance, args, kwargs):
        params = kwargs.pop("params", {})

        # make a copy of params in case the caller reuses them for some reason
        params = params.copy() if params is not None else {}

        cls_name, method_name = method.split(".", 1)

        # store a reference to the non-serialized body so we can use it in the connection layer
        body = kwargs.get("body")
        params[BODY_REF_NAME] = body
        params[API_METHOD_KEY_NAME] = method

        kwargs["params"] = params
        return wrapped(*args, **kwargs)
