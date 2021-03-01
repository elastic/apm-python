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

import time

import elasticapm
from elasticapm.conf import constants
from elasticapm.contrib.django.client import get_client as d_client
from elasticapm.contrib.flask import get_client as f_client
from elasticapm.instrumentation.packages.base import AbstractInstrumentedModule
from elasticapm.traces import capture_span, execution_context
from elasticapm.utils.disttracing import TraceParent


def django_client():
    _client = None
    try:
        _client = d_client()
    except Exception:  # in some cases we get a different exception
        return None
    return _client


def get_client():
    if django_client():
        return django_client()
    elif f_client():
        return f_client()


def get_trace_id(result):
    for i in result:
        if isinstance(i, list):
            if isinstance(i[0], tuple) and len(i[0]) == 2:
                for k, v in i:
                    k_str = str(k)
                    if k_str == "trace":
                        return v
    return None


class KafkaInstrumentation(AbstractInstrumentedModule):

    instrument_list = [
        ("kafka", "KafkaProducer.send"),
        ("kafka", "KafkaConsumer.next"),
    ]
    provider_name = "kafka"
    name = "kafka"

    def _trace_send(
        self,
        method,
        topic,
        value=None,
        key=None,
        headers=None,
        partition=None,
        timestamp_ms=None,
        action="send",
    ):
        span_name = "KafkaProducer#send to " + topic
        service = self.destination_info["service"]
        service["resource"] = service["resource"] + topic
        self.destination_info["service"] = service
        with capture_span(
            name=span_name,
            span_type="messaging",
            span_subtype=self.provider_name,
            span_action=action,
            extra={
                "message": {"queue": {"name": topic}},
                "destination": self.destination_info,
            },
        ) as span:
            transaction = execution_context.get_transaction()
            if transaction:
                tp = transaction.trace_parent
                tp_string = tp.to_string()
                # ID REPLACE SECTION START
                new_tp_string = tp_string.replace(transaction.id, span.id)
            if headers:
                headers.append(("trace", bytes(new_tp_string)))
            else:
                headers = [("trace", bytes(new_tp_string))]
            # ID REPLACE SECTION STOP
            result = method(
                topic,
                value=value,
                key=key,
                headers=headers,
                partition=partition,
                timestamp_ms=timestamp_ms,
            )
            return result

    def call_if_sampling(self, module, method, wrapped, instance, args, kwargs):
        # Contrasting to the superclass implementation, we *always* want to
        # return a proxied connection, even if there is no ongoing elasticapm
        # transaction yet. This ensures that we instrument the cursor once
        # the transaction started.
        return self.call(module, method, wrapped, instance, args, kwargs)

    def call(self, module, method, wrapped, instance, args, kwargs):
        topic = None
        destination_info = {
            "service": {"name": "kafka", "resource": "kafka/", "type": "messaging"},
        }
        self.destination_info = destination_info
        if method == "KafkaProducer.send":
            address = None
            port = None
            time_start = time.time()
            while not instance._metadata.controller:
                if time.time() - time_start > 1:
                    break
                continue
            if instance:
                if instance._metadata.controller:
                    address = instance._metadata.controller[1]
                    port = instance._metadata.controller[2]
            self.destination_info["port"] = port
            self.destination_info["address"] = address
            topic = args[0].encode("utf-8")
            transaction = execution_context.get_transaction()
            if transaction:
                return self._trace_send(wrapped, topic, **kwargs)

        if method == "KafkaConsumer.next":
            transaction = execution_context.get_transaction()
            if transaction and transaction.transaction_type != "messaging":
                action = "consume"
                with capture_span(
                    name="consumer",
                    span_type="messaging",
                    span_subtype=self.provider_name,
                    span_action=action,
                    extra={
                        "message": {"queue": {"name": ""}},
                        "destination": self.destination_info,
                    },
                ) as span:
                    result = wrapped(*args, **kwargs)
                    topic = result[0]
                    new_trace_id = get_trace_id(result)
                    service = self.destination_info["service"]
                    service["resource"] = service["resource"] + topic
                    span.context["message"]["queue"]["name"] = topic
                    span.context["destination"]["service"] = service
                    span.name = "KafkaConsumer#receive from " + topic
                    transaction.trace_parent = TraceParent.from_string(new_trace_id)
                    return result
            else:
                client = get_client()
                if transaction and transaction.transaction_type == "messaging":
                    client.end_transaction()

                result = wrapped(*args, **kwargs)
                topic = result[0]
                new_trace_id = None
                new_trace_id = get_trace_id(result)

                client.begin_transaction("messaging", trace_parent=None)
                transaction = execution_context.get_transaction()
                if result.timestamp_type == 0:
                    current_time_millis = int(round(time.time() * 1000))
                    age = current_time_millis - result.timestamp
                    transaction.context = {
                        "message": {"age": {"ms": age}, "queue": {"name": topic}}
                    }
                if new_trace_id:
                    transaction.trace_parent = TraceParent.from_string(new_trace_id)
                t_name = "Kafka record from " + topic
                elasticapm.set_transaction_name(t_name, override=True)
                res = constants.OUTCOME.SUCCESS
                elasticapm.set_transaction_result(res, override=False)
                return result
