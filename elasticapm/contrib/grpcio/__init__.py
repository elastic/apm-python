#  BSD 3-Clause License
#
#  Copyright (c) 2012, the Sentry Team, see AUTHORS for more details
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


from __future__ import absolute_import

import logging

import grpc
import elasticapm
import elasticapm.instrumentation.control
from elasticapm.base import Client
from elasticapm.conf import setup_logging
from elasticapm.handlers.logging import LoggingHandler
from elasticapm.traces import execution_context
from elasticapm.utils.disttracing import TraceParent
from elasticapm.utils.logging import get_logger
from elasticapm.utils import get_url_dict
from functools import wraps


logger = get_logger("elasticapm.errors.client")


def make_client(client_cls, config, **defaults):

    if "framework_name" not in defaults:
        defaults["framework_name"] = "grpc"
        defaults["framework_version"] = grpc.__version__

    client = client_cls(config, **defaults)
    return client


class RequestHeaderValidatorInterceptor(grpc.ServerInterceptor):
    """
    grpc application for Elastic APM

    Look up configuration from ``os.environ.get('ELASTIC_APM_APP_NAME')`` and
    ``os.environ.get('ELASTIC_APM_SECRET_TOKEN')``::

    >>> ELASTIC_APM_CONFIG = {
    ...  "SERVICE_NAME": "grpcapp",
    ... "SECRET_TOKEN": "changeme",
    ... }

    >>> interceptor = RequestHeaderValidatorInterceptor(
    ... config=ELASTIC_APM_CONFIG,
    ... )

    Automatically configure logging::

     >>> interceptor = RequestHeaderValidatorInterceptor(
    ... config=ELASTIC_APM_CONFIG,
    ... logging=True
    ... )

    May start your grpc server with:
    >>> grpc.server(
    ... futures.ThreadPoolExecutor(max_workers=10),
    ... interceptors=(interceptor,)

    Customize response status:
    >>> ELASTIC_APM_CONFIG = {
    ...  "SERVICE_NAME": "grpcapp",
    ... "SECRET_TOKEN": "changeme",
    ... "RESULT_HANDLER": lambda msg: "You" in msg and "SUCC" or "FAIL"
    ... }

    The RESULT_HANDLER is a function for handler response.message for determining success or failed
    should be a callable object that follow:
    Callable[str] -> bool
    """
    def __init__(self, client=None, client_cls=Client, logging=False, config={}, **defaults):
        self.logging = logging
        self.config = config
        self.client = client
        self.client_cls = client_cls

        if not self.client:
            self.client = make_client(
                self.client_cls, self.config, **defaults)
        self.setup_logging()
        self.setup_instrument()

    def setup_logging(self):
        if self.logging or self.logging is logging.NOTSET:
            if self.logging is not True:
                kwargs = {"level": self.logging}
            else:
                kwargs = {}
            setup_logging(LoggingHandler(self.client, **kwargs))

    def setup_instrument(self):
        if self.client.config.instrument and self.client.config.enabled:
            elasticapm.instrumentation.control.instrument()
        try:
            from elasticapm.contrib.celery import register_instrumentation
            register_instrumentation(self.client)
        except ImportError:
            pass
        else:
            logger.debug(
                "Skipping instrumentation. INSTRUMENT is set to False.")

    def wrap_response(self, response_future, method, tx):
        def wrap_method(fn):
            @wraps(fn)
            def _(*args, **kwargs):
                try:
                    execution_context.set_transaction(tx)
                    request = args[0]
                    elasticapm.set_context({
                        "url": get_url_dict(method),
                        "body": str(request),
                        "method": "GRPC"
                    }, "request")
                    result = fn(*args, **kwargs)
                except Exception:
                    logger.error(
                        "Faield to set context",
                        exc_info=True
                    )
                self.request_finished(result)
                return result
            return _

        multable_response = response_future._asdict()

        if response_future.unary_unary:
            multable_response["unary_unary"] = wrap_method(
                response_future.unary_unary)
        if response_future.unary_stream:
            multable_response["unary_stream"] = wrap_method(
                response_future.unary_stream)
        if response_future.stream_unary:
            multable_response["unary_stream"] = wrap_method(
                response_future.steam_unary)
        if response_future.stream_stream:
            multable_response["stream_stream"] = wrap_method(
                response_future.steam_steam)
        return response_future.__class__(**multable_response)

    def with_transaction(self, handler_call_details, continuation):
        if self.client.config.debug:
            return continuation(handler_call_details)

        self.request_started(handler_call_details)

        tx = execution_context.get_transaction()
        response_future = continuation(handler_call_details)
        # Cross thread here

        method = handler_call_details.method
        elasticapm.set_transaction_name("gRPC %s" % method)
        ret = self.wrap_response(response_future, method, tx)
        return ret

    def request_started(self, handler_call_details):
        try:
            meta_data = handler_call_details.invocation_metadata[0]._asdict()
            trace_parent = TraceParent.from_headers(meta_data)
            self.client.begin_transaction("request", trace_parent=trace_parent)
        except Exception:
            logger.error("Exception durinng handle request", exc_info=True)


    def request_finished(self, result):
        try:
            result_handler = self.config.get("RESULT_HANDLER", lambda x: bool(x))
            result = result_handler(result) and "SUCC" or "FAIL"
            elasticapm.set_transaction_result(result, override=False)
            self.client.end_transaction()
        except Exception:
            logger.error("Exception durinng handle response", exc_info=True)

    def intercept_service(self, continuation, handler_call_details):
        try:
            return self.with_transaction(handler_call_details, continuation)
        except Exception:
            logger.error("Exception during handle transaction", exc_info)
            return continuation(handler_call_details)
