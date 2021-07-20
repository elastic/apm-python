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

from opentracing.span import Span as OTSpanBase
from opentracing.span import SpanContext as OTSpanContextBase

from elasticapm import traces
from elasticapm.traces import DroppedSpan
from elasticapm.utils import compat, get_url_dict
from elasticapm.utils.logging import get_logger

try:
    # opentracing-python 2.1+
    from opentracing import logs as ot_logs
    from opentracing import tags
except ImportError:
    # opentracing-python <2.1
    from opentracing.ext import tags

    ot_logs = None


logger = get_logger("elasticapm.contrib.opentracing")


class OTSpan(OTSpanBase):
    def __init__(self, tracer, context, elastic_apm_ref):
        super(OTSpan, self).__init__(tracer, context)
        self.elastic_apm_ref = elastic_apm_ref
        self.is_transaction = isinstance(elastic_apm_ref, traces.Transaction)
        self.is_dropped = isinstance(elastic_apm_ref, traces.DroppedSpan)
        if not context.span:
            context.span = self

    def log_kv(self, key_values, timestamp=None):
        exc_type, exc_val, exc_tb = None, None, None
        if "python.exception.type" in key_values:
            exc_type = key_values["python.exception.type"]
            exc_val = key_values.get("python.exception.val")
            exc_tb = key_values.get("python.exception.tb")
        elif ot_logs and key_values.get(ot_logs.EVENT) == tags.ERROR:
            exc_type = key_values[ot_logs.ERROR_KIND]
            exc_val = key_values.get(ot_logs.ERROR_OBJECT)
            exc_tb = key_values.get(ot_logs.STACK)
        else:
            logger.debug("Can't handle non-exception type opentracing logs")
        if exc_type:
            agent = self.tracer._agent
            agent.capture_exception(exc_info=(exc_type, exc_val, exc_tb))
        return self

    def set_operation_name(self, operation_name):
        self.elastic_apm_ref.name = operation_name
        return self

    def set_tag(self, key, value):
        if self.is_transaction:
            if key == "type":
                self.elastic_apm_ref.transaction_type = value
            elif key == "result":
                self.elastic_apm_ref.result = value
            elif key == tags.HTTP_STATUS_CODE:
                self.elastic_apm_ref.result = "HTTP {}xx".format(compat.text_type(value)[0])
                traces.set_context({"status_code": value}, "response")
            elif key == "user.id":
                traces.set_user_context(user_id=value)
            elif key == "user.username":
                traces.set_user_context(username=value)
            elif key == "user.email":
                traces.set_user_context(email=value)
            elif key == tags.HTTP_URL:
                traces.set_context({"url": get_url_dict(value)}, "request")
            elif key == tags.HTTP_METHOD:
                traces.set_context({"method": value}, "request")
            elif key == tags.COMPONENT:
                traces.set_context({"framework": {"name": value}}, "service")
            else:
                self.elastic_apm_ref.label(**{key: value})
        elif not self.is_dropped:
            if key.startswith("db."):
                span_context = self.elastic_apm_ref.context or {}
                if "db" not in span_context:
                    span_context["db"] = {}
                if key == tags.DATABASE_STATEMENT:
                    span_context["db"]["statement"] = value
                elif key == tags.DATABASE_USER:
                    span_context["db"]["user"] = value
                elif key == tags.DATABASE_TYPE:
                    span_context["db"]["type"] = value
                    self.elastic_apm_ref.type = "db." + value
                else:
                    self.elastic_apm_ref.label(**{key: value})
                self.elastic_apm_ref.context = span_context
            elif key == tags.SPAN_KIND:
                self.elastic_apm_ref.type = value
            else:
                self.elastic_apm_ref.label(**{key: value})
        return self

    def finish(self, finish_time=None):
        if finish_time is not None and not isinstance(self.elastic_apm_ref, DroppedSpan):
            duration = finish_time - self.elastic_apm_ref.timestamp
        else:
            duration = None
        if self.is_transaction:
            self.tracer._agent.end_transaction(duration=duration)
        elif not self.is_dropped:
            self.elastic_apm_ref.transaction.end_span(duration=duration)


class OTSpanContext(OTSpanContextBase):
    def __init__(self, trace_parent, span=None):
        self.trace_parent = trace_parent
        self.span = span
