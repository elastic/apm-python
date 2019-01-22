from opentracing import Format, InvalidCarrierException, SpanContextCorruptedException, UnsupportedFormatException
from opentracing.scope_managers import ThreadLocalScopeManager
from opentracing.tracer import ReferenceType
from opentracing.tracer import Tracer as TracerBase

import elasticapm
from elasticapm import instrument, traces
from elasticapm.conf import constants
from elasticapm.contrib.opentracing.span import OTSpan, OTSpanContext
from elasticapm.utils import compat, disttracing


class Tracer(TracerBase):
    _elasticapm_client_class = elasticapm.Client

    def __init__(self, client_instance=None, config=None, scope_manager=None):
        self._agent = client_instance or self._elasticapm_client_class(config=config)
        self._scope_manager = scope_manager or ThreadLocalScopeManager()
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
        if not transaction:
            trace_parent = parent_context.trace_parent if parent_context else None
            transaction = self._agent.begin_transaction("opentracing", trace_parent=trace_parent)
            transaction.name = operation_name
            span_context = OTSpanContext(trace_parent=transaction.trace_parent)
            ot_span = OTSpan(self, span_context, transaction)
        else:
            parent_span_id = (
                parent_context.span.elastic_apm_ref.id
                if parent_context and parent_context.span and not parent_context.span.is_transaction
                else None
            )
            span = transaction.begin_span(operation_name, None, parent_span_id=parent_span_id)
            trace_parent = parent_context.trace_parent if parent_context else transaction.trace_parent
            span_context = OTSpanContext(trace_parent=trace_parent.copy_from(span_id=span.id))
            ot_span = OTSpan(self, span_context, span)
        if tags:
            for k, v in compat.iteritems(tags):
                ot_span.set_tag(k, v)
        return ot_span

    def extract(self, format, carrier):
        if format in (Format.HTTP_HEADERS, Format.TEXT_MAP):
            if constants.TRACEPARENT_HEADER_NAME not in carrier:
                raise SpanContextCorruptedException("could not extract span context from carrier")
            trace_parent = disttracing.TraceParent.from_string(carrier[constants.TRACEPARENT_HEADER_NAME])
            return OTSpanContext(trace_parent=trace_parent)
        raise UnsupportedFormatException

    def inject(self, span_context, format, carrier):
        if format in (Format.HTTP_HEADERS, Format.TEXT_MAP):
            if not isinstance(carrier, dict):
                raise InvalidCarrierException("carrier for {} format should be dict-like".format(format))
            carrier[constants.TRACEPARENT_HEADER_NAME] = span_context.trace_parent.to_ascii()
        raise UnsupportedFormatException