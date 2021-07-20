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

import warnings

from opentracing import Format, InvalidCarrierException, SpanContextCorruptedException, UnsupportedFormatException
from opentracing.scope_managers import ThreadLocalScopeManager
from opentracing.tracer import ReferenceType
from opentracing.tracer import Tracer as TracerBase

import elasticapm
from elasticapm import get_client, instrument, traces
from elasticapm.conf import constants
from elasticapm.contrib.opentracing.span import OTSpan, OTSpanContext
from elasticapm.utils import compat, disttracing
from elasticapm.utils.time import SYSTEM_TO_MONOTONIC


class Tracer(TracerBase):
    def __init__(self, client_instance=None, config=None, scope_manager=None):
        self._agent = client_instance or get_client() or elasticapm.Client(config=config)
        if scope_manager and not isinstance(scope_manager, ThreadLocalScopeManager):
            warnings.warn(
                "Currently, the Elastic APM opentracing bridge only supports the ThreadLocalScopeManager. "
                "Usage of other scope managers will lead to unpredictable results."
            )
        self._scope_manager = scope_manager or ThreadLocalScopeManager()
        if self._agent.config.instrument and self._agent.config.enabled:
            instrument()

    def start_active_span(
        self,
        operation_name,
        child_of=None,
        references=None,
        tags=None,
        start_time=None,
        ignore_active_span=False,
        finish_on_close=True,
    ):
        ot_span = self.start_span(
            operation_name,
            child_of=child_of,
            references=references,
            tags=tags,
            start_time=start_time,
            ignore_active_span=ignore_active_span,
        )
        scope = self._scope_manager.activate(ot_span, finish_on_close)
        return scope

    def start_span(
        self, operation_name=None, child_of=None, references=None, tags=None, start_time=None, ignore_active_span=False
    ):
        if isinstance(child_of, OTSpanContext):
            parent_context = child_of
        elif isinstance(child_of, OTSpan):
            parent_context = child_of.context
        elif references and references[0].type == ReferenceType.CHILD_OF:
            parent_context = references[0].referenced_context
        else:
            parent_context = None
        transaction = traces.execution_context.get_transaction()
        start_time_monotonic = None
        if start_time is not None:
            start_time_monotonic = SYSTEM_TO_MONOTONIC.convert_timestamp(start_time)
        if not transaction or ignore_active_span:
            trace_parent = parent_context.trace_parent if parent_context else None
            transaction = self._agent.begin_transaction(
                "custom", trace_parent=trace_parent, start=start_time_monotonic, start_timestamp=start_time
            )
            transaction.name = operation_name
            span_context = OTSpanContext(trace_parent=transaction.trace_parent)
            ot_span = OTSpan(self, span_context, transaction)
        else:
            # to allow setting an explicit parent span, we check if the parent_context is set
            # and if it is a span. In all other cases, the parent is found implicitly through the
            # execution context.
            parent_span_id = (
                parent_context.span.elastic_apm_ref.id
                if parent_context and parent_context.span and not parent_context.span.is_transaction
                else None
            )
            span = transaction._begin_span(
                operation_name, None, parent_span_id=parent_span_id, start=start_time_monotonic
            )
            trace_parent = parent_context.trace_parent if parent_context else transaction.trace_parent
            span_context = OTSpanContext(trace_parent=trace_parent.copy_from(span_id=span.id))
            ot_span = OTSpan(self, span_context, span)
        if tags:
            for k, v in compat.iteritems(tags):
                ot_span.set_tag(k, v)
        return ot_span

    def extract(self, format, carrier):
        if format in (Format.HTTP_HEADERS, Format.TEXT_MAP):
            trace_parent = disttracing.TraceParent.from_headers(carrier)
            if not trace_parent:
                raise SpanContextCorruptedException("could not extract span context from carrier")
            return OTSpanContext(trace_parent=trace_parent)
        raise UnsupportedFormatException

    def inject(self, span_context, format, carrier):
        if format in (Format.HTTP_HEADERS, Format.TEXT_MAP):
            if not isinstance(carrier, dict):
                raise InvalidCarrierException("carrier for {} format should be dict-like".format(format))
            val = span_context.trace_parent.to_ascii()
            carrier[constants.TRACEPARENT_HEADER_NAME] = val
            if self._agent.config.use_elastic_traceparent_header:
                carrier[constants.TRACEPARENT_LEGACY_HEADER_NAME] = val
            return
        raise UnsupportedFormatException
