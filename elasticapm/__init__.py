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
import sys

from elasticapm.base import Client, get_client  # noqa: F401
from elasticapm.conf import setup_logging  # noqa: F401
from elasticapm.instrumentation.control import instrument, uninstrument  # noqa: F401
from elasticapm.traces import (  # noqa: F401
    capture_span,
    transaction,
    get_span_id,
    get_trace_id,
    get_trace_parent_header,
    get_transaction_id,
    label,
    set_context,
    set_custom_context,
    set_transaction_name,
    set_transaction_outcome,
    set_transaction_result,
    set_user_context,
)
from elasticapm.utils.disttracing import trace_parent_from_headers, trace_parent_from_string  # noqa: F401

__all__ = ("VERSION", "Client")

try:
    try:
        VERSION = __import__("importlib.metadata").metadata.version("elastic-apm")
    except ImportError:
        VERSION = __import__("pkg_resources").get_distribution("elastic-apm").version
except Exception:
    VERSION = "unknown"


if sys.version_info <= (3, 5):
    raise DeprecationWarning("The Elastic APM agent requires Python 3.6+")

from elasticapm.contrib.asyncio.traces import async_capture_span  # noqa: F401
