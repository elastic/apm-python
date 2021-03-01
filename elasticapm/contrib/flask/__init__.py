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

import flask
from flask import request, signals

import elasticapm
import elasticapm.instrumentation.control
from elasticapm.base import Client
from elasticapm.conf import constants, setup_logging
from elasticapm.contrib.flask.utils import get_data_from_request, get_data_from_response
from elasticapm.handlers.logging import LoggingHandler
from elasticapm.traces import execution_context
from elasticapm.utils import build_name_with_http_method_prefix
from elasticapm.utils.disttracing import TraceParent
from elasticapm.utils.logging import get_logger

logger = get_logger("elasticapm.errors.client")

_client = None


def make_client(client_cls, app, **defaults):
    config = app.config.get("ELASTIC_APM", {})

    if "framework_name" not in defaults:
        defaults["framework_name"] = "flask"
        defaults["framework_version"] = getattr(flask, "__version__", "<0.7")

    client = client_cls(config, **defaults)
    global _client
    _client = client
    return client


def get_client():
    global _client
    return _client


class ElasticAPM(object):
    """
    Flask application for Elastic APM.

    Look up configuration from ``os.environ.get('ELASTIC_APM_APP_NAME')`` and
    ``os.environ.get('ELASTIC_APM_SECRET_TOKEN')``::

    >>> elasticapm = ElasticAPM(app)

    Pass an arbitrary APP_NAME and SECRET_TOKEN::

    >>> elasticapm = ElasticAPM(app, service_name='myapp', secret_token='asdasdasd')

    Pass an explicit client::

    >>> elasticapm = ElasticAPM(app, client=client)

    Automatically configure logging::

    >>> elasticapm = ElasticAPM(app, logging=True)

    Capture an exception::

    >>> try:
    >>>     1 / 0
    >>> except ZeroDivisionError:
    >>>     elasticapm.capture_exception()

    Capture a message::

    >>> elasticapm.capture_message('hello, world!')
    """

    def __init__(self, app=None, client=None, client_cls=Client, logging=False, **defaults):
        self.app = app
        self.logging = logging
        self.client_cls = client_cls
        self.client = client

        if app:
            self.init_app(app, **defaults)

    def handle_exception(self, *args, **kwargs):
        if not self.client:
            return

        if self.app.debug and not self.client.config.debug:
            return

        self.client.capture_exception(
            exc_info=kwargs.get("exc_info"),
            context={"request": get_data_from_request(request, self.client.config, constants.ERROR)},
            custom={"app": self.app},
            handled=False,
        )
        # End the transaction here, as `request_finished` won't be called when an
        # unhandled exception occurs.
        #
        # Unfortunately, that also means that we can't capture any response data,
        # as the response isn't ready at this point in time.
        self.client.end_transaction(result="HTTP 5xx")

    def init_app(self, app, **defaults):
        self.app = app
        if not self.client:
            self.client = make_client(self.client_cls, app, **defaults)

        # 0 is a valid log level (NOTSET), so we need to check explicitly for it
        if self.logging or self.logging is logging.NOTSET:
            if self.logging is not True:
                kwargs = {"level": self.logging}
            else:
                kwargs = {}
            setup_logging(LoggingHandler(self.client, **kwargs))

        signals.got_request_exception.connect(self.handle_exception, sender=app, weak=False)

        try:
            from elasticapm.contrib.celery import register_exception_tracking

            register_exception_tracking(self.client)
        except ImportError:
            pass

        # Instrument to get spans
        if self.client.config.instrument and self.client.config.enabled:
            elasticapm.instrumentation.control.instrument()
            signals.request_started.connect(self.request_started, sender=app)
            signals.request_finished.connect(self.request_finished, sender=app)
            try:
                from elasticapm.contrib.celery import register_instrumentation

                register_instrumentation(self.client)
            except ImportError:
                pass
        else:
            logger.debug("Skipping instrumentation. INSTRUMENT is set to False.")

        @app.context_processor
        def rum_tracing():
            """
            Adds APM related IDs to the context used for correlating the backend transaction with the RUM transaction
            """
            transaction = execution_context.get_transaction()
            if transaction and transaction.trace_parent:
                return {
                    "apm": {
                        "trace_id": transaction.trace_parent.trace_id,
                        "span_id": lambda: transaction.ensure_parent_id(),
                        "is_sampled": transaction.is_sampled,
                        "is_sampled_js": "true" if transaction.is_sampled else "false",
                    }
                }
            return {}

    def request_started(self, app):
        if (not self.app.debug or self.client.config.debug) and not self.client.should_ignore_url(request.path):
            trace_parent = TraceParent.from_headers(request.headers)
            self.client.begin_transaction("request", trace_parent=trace_parent)
            elasticapm.set_context(
                lambda: get_data_from_request(request, self.client.config, constants.TRANSACTION), "request"
            )
            rule = request.url_rule.rule if request.url_rule is not None else ""
            rule = build_name_with_http_method_prefix(rule, request)
            elasticapm.set_transaction_name(rule, override=False)

    def request_finished(self, app, response):
        if not self.app.debug or self.client.config.debug:
            elasticapm.set_context(
                lambda: get_data_from_response(response, self.client.config, constants.TRANSACTION), "response"
            )
            if response.status_code:
                result = "HTTP {}xx".format(response.status_code // 100)
                elasticapm.set_transaction_outcome(http_status_code=response.status_code, override=False)
            else:
                result = response.status
                elasticapm.set_transaction_outcome(http_status_code=response.status, override=False)
            elasticapm.set_transaction_result(result, override=False)
            # Instead of calling end_transaction here, we defer the call until the response is closed.
            # This ensures that we capture things that happen until the WSGI server closes the response.
            response.call_on_close(self.client.end_transaction)

    def capture_exception(self, *args, **kwargs):
        assert self.client, "capture_exception called before application configured"
        return self.client.capture_exception(*args, **kwargs)

    def capture_message(self, *args, **kwargs):
        assert self.client, "capture_message called before application configured"
        return self.client.capture_message(*args, **kwargs)
