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
"""
Instrumentation for Tornado
"""
import elasticapm
from elasticapm.contrib.tornado.utils import get_data_from_request, get_data_from_response
from elasticapm.instrumentation.packages.asyncio.base import AbstractInstrumentedModule, AsyncAbstractInstrumentedModule
from elasticapm.utils.disttracing import TraceParent


class TornadoRequestExecuteInstrumentation(AsyncAbstractInstrumentedModule):
    name = "tornado_request_execute"
    creates_transactions = True
    instrument_list = [("tornado.web", "RequestHandler._execute")]

    async def call(self, module, method, wrapped, instance, args, kwargs):
        request = instance.request
        trace_parent = TraceParent.from_headers(request.headers)
        client = instance.application.elasticapm_client
        client.begin_transaction("request", trace_parent=trace_parent)
        elasticapm.set_context(
            lambda: get_data_from_request(
                request,
                capture_body=client.config.capture_body in ("transactions", "all"),
                capture_headers=client.config.capture_headers,
            ),
            "request",
        )
        # TODO: Can we somehow incorporate the routing rule itself here?
        elasticapm.set_transaction_name("{} {}".format(request.method, type(instance).__name__), override=False)

        ret = await wrapped(*args, **kwargs)

        elasticapm.set_context(
            lambda: get_data_from_response(instance, capture_headers=client.config.capture_headers), "response"
        )
        result = "HTTP {}xx".format(instance.get_status() // 100)
        elasticapm.set_transaction_result(result, override=False)
        client.end_transaction()

        return ret


class TornadoHandleExceptionInstrumentation(AbstractInstrumentedModule):
    name = "tornado_handle_exception"

    instrument_list = [("tornado.web.RequestHandler", "_handle_exception")]

    async def call(self, module, method, wrapped, instance, args, kwargs):
        # FIXME
        return wrapped(*args, **kwargs)


class TornadoRenderInstrumentation(AbstractInstrumentedModule):
    name = "tornado_render"

    instrument_list = [("tornado.web.RequestHandler", "render")]

    async def call(self, module, method, wrapped, instance, args, kwargs):
        # FIXME
        return wrapped(*args, **kwargs)