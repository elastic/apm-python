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

from inspect import isawaitable, iscoroutinefunction

from sanic.handlers import ErrorHandler
from sanic.log import logger
from sanic.response import text

from elasticapm.contrib.sanic.sanic_types import ApmHandlerType


class ElasticAPMPatchedErrorHandler(ErrorHandler):
    """
    This is a monkey patchable instance of the Sanic's Error handler infra. Current implementation of Sanic doesn't
    let you chain exception handlers by raising exception chains further down the line. In order to bypass this
    limitation, we monkey patch the current implementation. We invoke the instrumentation function first and then
    chain the exception down to the original handler so that we don't get in the way of standard exception handling.
    """

    def __init__(self, current_handler: ErrorHandler):
        super(ElasticAPMPatchedErrorHandler, self).__init__()
        self._current_handler = current_handler  # type: ErrorHandler
        self._apm_handler = None  # type: ApmHandlerType

    def add(self, exception, handler):
        self._current_handler.add(exception, handler)

    def lookup(self, exception):
        return self._current_handler.lookup(exception)

    def setup_apm_handler(self, apm_handler: ApmHandlerType, force: bool = False):
        if self._apm_handler is None or force:
            self._apm_handler = apm_handler

    async def _patched_response(self, request, exception):
        await self._apm_handler(request, exception)
        handler = self._current_handler.lookup(exception)
        response = None
        try:
            if handler:
                response = handler(request, exception)
            if response is None:
                response = self._current_handler.default(request, exception)
        except Exception:
            try:
                url = repr(request.url)
            except AttributeError:
                url = "unknown"
            response_message = "Exception raised in exception handler " '"%s" for uri: %s'
            logger.exception(response_message, handler.__name__, url)

            if self.debug:
                return text(response_message % (handler.__name__, url), 500)
            else:
                return text("An error occurred while handling an error", 500)
        if iscoroutinefunction(response) or isawaitable(response):
            return await response
        return response

    def response(self, request, exception):
        return self._patched_response(request=request, exception=exception)

    def default(self, request, exception):
        return self._patched_response(request=request, exception=exception)
