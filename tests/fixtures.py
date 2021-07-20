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

import codecs
import gzip
import json
import logging
import logging.handlers
import os
import random
import socket
import sys
import tempfile
import time
import zlib
from collections import defaultdict

import jsonschema
import mock
import pytest
from pytest_localserver.http import ContentServer
from werkzeug.wrappers import Request, Response

import elasticapm
from elasticapm.base import Client
from elasticapm.conf.constants import SPAN
from elasticapm.traces import execution_context
from elasticapm.transport.http_base import HTTPTransportBase
from elasticapm.utils import compat
from elasticapm.utils.threading import ThreadManager

try:
    from urllib.request import pathname2url
except ImportError:
    # Python 2
    from urllib import pathname2url

cur_dir = os.path.dirname(os.path.realpath(__file__))

ERRORS_SCHEMA = os.path.join(cur_dir, "upstream", "json-specs", "error.json")
TRANSACTIONS_SCHEMA = os.path.join(cur_dir, "upstream", "json-specs", "transaction.json")
SPAN_SCHEMA = os.path.join(cur_dir, "upstream", "json-specs", "span.json")
METRICSET_SCHEMA = os.path.join(cur_dir, "upstream", "json-specs", "metricset.json")
METADATA_SCHEMA = os.path.join(cur_dir, "upstream", "json-specs", "metadata.json")


with codecs.open(ERRORS_SCHEMA, encoding="utf8") as errors_json, codecs.open(
    TRANSACTIONS_SCHEMA, encoding="utf8"
) as transactions_json, codecs.open(SPAN_SCHEMA, encoding="utf8") as span_json, codecs.open(
    METRICSET_SCHEMA, encoding="utf8"
) as metricset_json, codecs.open(
    METADATA_SCHEMA, encoding="utf8"
) as metadata_json:
    VALIDATORS = {
        "error": jsonschema.Draft4Validator(
            json.load(errors_json),
            resolver=jsonschema.RefResolver(
                base_uri="file:" + pathname2url(ERRORS_SCHEMA), referrer="file:" + pathname2url(ERRORS_SCHEMA)
            ),
        ),
        "transaction": jsonschema.Draft4Validator(
            json.load(transactions_json),
            resolver=jsonschema.RefResolver(
                base_uri="file:" + pathname2url(TRANSACTIONS_SCHEMA),
                referrer="file:" + pathname2url(TRANSACTIONS_SCHEMA),
            ),
        ),
        "span": jsonschema.Draft4Validator(
            json.load(span_json),
            resolver=jsonschema.RefResolver(
                base_uri="file:" + pathname2url(SPAN_SCHEMA), referrer="file:" + pathname2url(SPAN_SCHEMA)
            ),
        ),
        "metricset": jsonschema.Draft4Validator(
            json.load(metricset_json),
            resolver=jsonschema.RefResolver(
                base_uri="file:" + pathname2url(METRICSET_SCHEMA), referrer="file:" + pathname2url(METRICSET_SCHEMA)
            ),
        ),
        "metadata": jsonschema.Draft4Validator(
            json.load(metadata_json),
            resolver=jsonschema.RefResolver(
                base_uri="file:" + pathname2url(METADATA_SCHEMA), referrer="file:" + pathname2url(METADATA_SCHEMA)
            ),
        ),
    }


class ValidatingWSGIApp(ContentServer):
    def __init__(self, **kwargs):
        self.skip_validate = kwargs.pop("skip_validate", False)
        super(ValidatingWSGIApp, self).__init__(**kwargs)
        self.payloads = []
        self.responses = []

    def __call__(self, environ, start_response):
        content = self.content
        request = Request(environ)
        self.requests.append(request)
        data = request.data
        if request.content_encoding == "deflate":
            data = zlib.decompress(data)
        elif request.content_encoding == "gzip":
            with gzip.GzipFile(fileobj=compat.BytesIO(data)) as f:
                data = f.read()
        data = data.decode(request.charset)
        if request.content_type == "application/x-ndjson":
            data = [json.loads(line) for line in data.split("\n") if line]
        self.payloads.append(data)
        code = 202
        success = 0
        fail = 0
        if not self.skip_validate:
            for line in data:
                item_type, item = list(line.items())[0]
                validator = VALIDATORS[item_type]
                try:
                    validator.validate(item)
                    success += 1
                except jsonschema.ValidationError as e:
                    fail += 1
                    content += "/".join(map(compat.text_type, e.absolute_schema_path)) + ": " + e.message + "\n"
            code = 202 if not fail else 400
        response = Response(status=code)
        response.headers.clear()
        response.headers.extend(self.headers)
        response.data = content
        self.responses.append({"code": code, "content": content})
        return response(environ, start_response)


@pytest.fixture
def mock_client_excepthook():
    with mock.patch("tests.fixtures.TempStoreClient._excepthook") as m:
        yield m


@pytest.fixture
def mock_client_capture_exception():
    with mock.patch("tests.fixtures.TempStoreClient.capture_exception") as m:
        yield m


@pytest.fixture
def original_exception_hook(request):
    mock_params = getattr(request, "param", {})
    original_excepthook = sys.excepthook
    mck = mock.Mock(side_effect=mock_params.get("side_effect"))
    sys.excepthook = mck
    yield mck
    sys.excepthook = original_excepthook


@pytest.fixture()
def elasticapm_client(request):
    original_exceptionhook = sys.excepthook
    client_config = getattr(request, "param", {})
    client_config.setdefault("service_name", "myapp")
    client_config.setdefault("secret_token", "test_key")
    client_config.setdefault("central_config", "false")
    client_config.setdefault("include_paths", ("*/tests/*",))
    client_config.setdefault("span_frames_min_duration", -1)
    client_config.setdefault("metrics_interval", "0ms")
    client_config.setdefault("cloud_provider", False)
    client = TempStoreClient(**client_config)
    yield client
    client.close()
    # clear any execution context that might linger around
    sys.excepthook = original_exceptionhook
    execution_context.set_transaction(None)
    execution_context.set_span(None)
    assert not client._transport.validation_errors


@pytest.fixture()
def elasticapm_client_log_file(request):
    original_exceptionhook = sys.excepthook
    client_config = getattr(request, "param", {})
    client_config.setdefault("service_name", "myapp")
    client_config.setdefault("secret_token", "test_key")
    client_config.setdefault("central_config", "false")
    client_config.setdefault("include_paths", ("*/tests/*",))
    client_config.setdefault("span_frames_min_duration", -1)
    client_config.setdefault("metrics_interval", "0ms")
    client_config.setdefault("cloud_provider", False)
    client_config.setdefault("log_level", "warning")

    root_logger = logging.getLogger()
    handler = logging.StreamHandler()
    root_logger.addHandler(handler)

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    client_config["log_file"] = tmp.name

    client = TempStoreClient(**client_config)
    yield client
    client.close()

    # delete our tmpfile
    logger = logging.getLogger("elasticapm")
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
    os.unlink(tmp.name)

    # Remove our streamhandler
    root_logger.removeHandler(handler)

    # clear any execution context that might linger around
    sys.excepthook = original_exceptionhook
    execution_context.set_transaction(None)
    execution_context.set_span(None)


@pytest.fixture()
def waiting_httpserver(httpserver):
    wait_for_http_server(httpserver)
    return httpserver


@pytest.fixture
def httpsserver_custom(request):
    """The returned ``httpsserver`` (note the additional S!) provides a
    threaded HTTP server instance similar to funcarg ``httpserver`` but with
    SSL encryption.
    """
    from pytest_localserver import https

    config = getattr(request, "param", {})
    key = os.path.join(cur_dir, "ca", config.get("key", "server.pem"))

    server = https.SecureContentServer(key=key, cert=key)
    server.start()
    request.addfinalizer(server.stop)
    return server


@pytest.fixture()
def waiting_httpsserver(httpsserver_custom):
    wait_for_http_server(httpsserver_custom)
    return httpsserver_custom


@pytest.fixture()
def validating_httpserver(request):
    config = getattr(request, "param", {})
    app = config.pop("app", ValidatingWSGIApp)
    server = app(**config)
    server.start()
    wait_for_http_server(server)
    request.addfinalizer(server.stop)
    return server


@pytest.fixture()
def sending_elasticapm_client(request, validating_httpserver):
    validating_httpserver.serve_content(code=202, content="", headers={"Location": "http://example.com/foo"})
    client_config = getattr(request, "param", {})
    client_config.setdefault("server_url", validating_httpserver.url)
    client_config.setdefault("service_name", "myapp")
    client_config.setdefault("secret_token", "test_key")
    client_config.setdefault("transport_class", "elasticapm.transport.http.Transport")
    client_config.setdefault("span_frames_min_duration", -1)
    client_config.setdefault("include_paths", ("*/tests/*",))
    client_config.setdefault("metrics_interval", "0ms")
    client_config.setdefault("central_config", "false")
    client_config.setdefault("server_version", (8, 0, 0))
    client = Client(**client_config)
    client.httpserver = validating_httpserver
    yield client
    client.close()
    # clear any execution context that might linger around
    execution_context.set_transaction(None)
    execution_context.set_span(None)


class DummyTransport(HTTPTransportBase):
    def __init__(self, url, *args, **kwargs):
        super(DummyTransport, self).__init__(url, *args, **kwargs)
        self.events = defaultdict(list)
        self.validation_errors = defaultdict(list)

    def queue(self, event_type, data, flush=False):
        self._flushed.clear()
        data = self._process_event(event_type, data)
        self.events[event_type].append(data)
        self._flushed.set()
        if data is not None:
            validator = VALIDATORS[event_type]
            try:
                validator.validate(data)
            except jsonschema.ValidationError as e:
                self.validation_errors[event_type].append(e.message)

    def start_thread(self, pid=None):
        # don't call the parent method, but the one from ThreadManager
        ThreadManager.start_thread(self, pid=pid)

    def stop_thread(self):
        pass

    def get_config(self, current_version=None, keys=None):
        return False, None, 30


class TempStoreClient(Client):
    def __init__(self, config=None, **inline):
        inline.setdefault("transport_class", "tests.fixtures.DummyTransport")
        super(TempStoreClient, self).__init__(config, **inline)

    @property
    def events(self):
        return self._transport.events

    def spans_for_transaction(self, transaction):
        """Test helper method to get all spans of a specific transaction"""
        return [span for span in self.events[SPAN] if span["transaction_id"] == transaction["id"]]


@pytest.fixture()
def temp_store_client():
    return TempStoreClient


@pytest.fixture()
def not_so_random():
    old_state = random.getstate()
    random.seed(42)
    yield
    random.setstate(old_state)


@pytest.fixture()
def instrument():
    elasticapm.instrument()
    yield
    elasticapm.uninstrument()


def wait_for_http_server(httpserver, timeout=30):
    start_time = time.time()
    while True:
        try:
            sock = socket.create_connection(httpserver.server_address, timeout=0.1)
            sock.close()
            break
        except socket.error:
            if time.time() - start_time > timeout:
                raise TimeoutError()
