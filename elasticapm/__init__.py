"""
elasticapm
~~~~~

:copyright: (c) 2011-2017 Elasticsearch

Large portions are
:copyright: (c) 2010 by the Sentry Team, see AUTHORS for more details.
:license: BSD, see LICENSE for more details.
"""
import sys

__all__ = ("VERSION", "Client")

try:
    VERSION = __import__("pkg_resources").get_distribution("elastic-apm").version
except Exception as e:
    VERSION = "unknown"

from elasticapm.base import Client
from elasticapm.conf import setup_logging  # noqa: F401
from elasticapm.instrumentation.control import instrument, uninstrument  # noqa: F401
from elasticapm.traces import set_context, set_custom_context  # noqa: F401
from elasticapm.traces import set_transaction_name, set_user_context, tag  # noqa: F401
from elasticapm.traces import set_transaction_result  # noqa: F401

if sys.version_info >= (3, 5):
    from elasticapm.contrib.asyncio.traces import async_capture_span as capture_span #  noqa: F401
else:
    from elasticapm.traces import capture_span #  noqa: F401
