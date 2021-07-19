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

import decimal
import re
from collections import namedtuple


def _starmatch_to_regex(pattern):
    """
    This is a duplicate of starmatch_to_regex() in utils/__init__.py

    Duplication to avoid circular imports
    """
    options = re.DOTALL
    # check if we are case sensitive
    if pattern.startswith("(?-i)"):
        pattern = pattern[5:]
    else:
        options |= re.IGNORECASE
    i, n = 0, len(pattern)
    res = []
    while i < n:
        c = pattern[i]
        i = i + 1
        if c == "*":
            res.append(".*")
        else:
            res.append(re.escape(c))
    return re.compile(r"(?:%s)\Z" % "".join(res), options)


EVENTS_API_PATH = "intake/v2/events"
PROFILE_API_PATH = "intake/v2/profile"
AGENT_CONFIG_PATH = "config/v1/agents"

TRACE_CONTEXT_VERSION = 0
TRACEPARENT_HEADER_NAME = "traceparent"
TRACEPARENT_LEGACY_HEADER_NAME = "elastic-apm-traceparent"
TRACESTATE_HEADER_NAME = "tracestate"

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

KEYWORD_MAX_LENGTH = 1024

HTTP_WITH_BODY = {"POST", "PUT", "PATCH", "DELETE"}

MASK = "[REDACTED]"

EXCEPTION_CHAIN_MAX_DEPTH = 50

ERROR = "error"
TRANSACTION = "transaction"
SPAN = "span"
METRICSET = "metricset"

LABEL_RE = re.compile('[.*"]')

HARDCODED_PROCESSORS = ["elasticapm.processors.add_context_lines_to_frames"]

BASE_SANITIZE_FIELD_NAMES_UNPROCESSED = [
    "password",
    "passwd",
    "pwd",
    "secret",
    "*key",
    "*token*",
    "*session*",
    "*credit*",
    "*card*",
    "authorization",
    "set-cookie",
]

BASE_SANITIZE_FIELD_NAMES = [_starmatch_to_regex(x) for x in BASE_SANITIZE_FIELD_NAMES_UNPROCESSED]

OUTCOME = namedtuple("OUTCOME", ["SUCCESS", "FAILURE", "UNKNOWN"])(
    SUCCESS="success", FAILURE="failure", UNKNOWN="unknown"
)

try:
    # Python 2
    LABEL_TYPES = (bool, int, long, float, decimal.Decimal)
except NameError:
    # Python 3
    LABEL_TYPES = (bool, int, float, decimal.Decimal)

TRACESTATE = namedtuple("TRACESTATE", ["SAMPLE_RATE"])(SAMPLE_RATE="s")
