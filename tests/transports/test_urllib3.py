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


import os

import mock
import pytest
import urllib3.poolmanager
from urllib3.exceptions import MaxRetryError, TimeoutError

from elasticapm.conf import constants
from elasticapm.transport.base import TransportException
from elasticapm.transport.http import Transport
from elasticapm.utils import compat

try:
    import urlparse
except ImportError:
    from urllib import parse as urlparse


def test_send(waiting_httpserver, elasticapm_client):
    waiting_httpserver.serve_content(code=202, content="", headers={"Location": "http://example.com/foo"})
    transport = Transport(waiting_httpserver.url, client=elasticapm_client)
    try:
        url = transport.send(compat.b("x"))
        assert url == "http://example.com/foo"
    finally:
        transport.close()


@mock.patch("urllib3.poolmanager.PoolManager.urlopen")
def test_timeout(mock_urlopen, elasticapm_client):
    transport = Transport("http://localhost", timeout=5, client=elasticapm_client)
    mock_urlopen.side_effect = MaxRetryError(None, None, reason=TimeoutError())
    try:
        with pytest.raises(TransportException) as exc_info:
            transport.send("x")
        assert "timeout" in str(exc_info.value)
    finally:
        transport.close()


def test_http_error(waiting_httpserver, elasticapm_client):
    waiting_httpserver.serve_content(code=418, content="I'm a teapot")
    transport = Transport(waiting_httpserver.url, client=elasticapm_client)
    try:
        with pytest.raises(TransportException) as exc_info:
            transport.send("x")
        for val in (418, "I'm a teapot"):
            assert str(val) in str(exc_info.value)
    finally:
        transport.close()


@mock.patch("urllib3.poolmanager.PoolManager.urlopen")
def test_generic_error(mock_urlopen, elasticapm_client):
    url, status, message, body = ("http://localhost:9999", 418, "I'm a teapot", "Nothing")
    transport = Transport(url, client=elasticapm_client)
    mock_urlopen.side_effect = Exception("Oopsie")
    try:
        with pytest.raises(TransportException) as exc_info:
            transport.send("x")
        assert "Oopsie" in str(exc_info.value)
    finally:
        transport.close()


def test_http_proxy_environment_variable():
    with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://example.com"}):
        transport = Transport("http://localhost:9999", client=None)
        try:
            assert isinstance(transport.http, urllib3.ProxyManager)
        finally:
            transport.close()


def test_https_proxy_environment_variable():
    with mock.patch.dict("os.environ", {"HTTPS_PROXY": "https://example.com"}):
        transport = Transport("http://localhost:9999", client=None)
        try:
            assert isinstance(transport.http, urllib3.poolmanager.ProxyManager)
        finally:
            transport.close()


def test_https_proxy_environment_variable_is_preferred():
    with mock.patch.dict("os.environ", {"https_proxy": "https://example.com", "HTTP_PROXY": "http://example.com"}):
        transport = Transport("http://localhost:9999", client=None)
        try:
            assert isinstance(transport.http, urllib3.poolmanager.ProxyManager)
            assert transport.http.proxy.scheme == "https"
        finally:
            transport.close()


def test_no_proxy_star():
    with mock.patch.dict("os.environ", {"HTTPS_PROXY": "https://example.com", "NO_PROXY": "*"}):
        transport = Transport("http://localhost:9999", client=None)
        try:
            assert not isinstance(transport.http, urllib3.poolmanager.ProxyManager)
        finally:
            transport.close()


def test_no_proxy_host():
    with mock.patch.dict("os.environ", {"HTTPS_PROXY": "https://example.com", "NO_PROXY": "localhost"}):
        transport = Transport("http://localhost:9999", client=None)
        try:
            assert not isinstance(transport.http, urllib3.poolmanager.ProxyManager)
        finally:
            transport.close()


def test_header_encodings(elasticapm_client):
    """
    Tests that headers are encoded as bytestrings. If they aren't,
    urllib assumes it needs to encode the data as well, which is already a zlib
    encoded bytestring, and explodes.
    """
    headers = {compat.text_type("X"): compat.text_type("V")}
    transport = Transport("http://localhost:9999", headers=headers, client=elasticapm_client)
    try:
        with mock.patch("elasticapm.transport.http.urllib3.PoolManager.urlopen") as mock_urlopen:
            mock_urlopen.return_value = mock.Mock(status=202)
            transport.send("")
        _, args, kwargs = mock_urlopen.mock_calls[0]
        if compat.PY2:
            assert isinstance(args[1], compat.binary_type)
        for k, v in kwargs["headers"].items():
            assert isinstance(k, compat.binary_type)
            assert isinstance(v, compat.binary_type)
    finally:
        transport.close()


def test_ssl_verify_fails(waiting_httpsserver, elasticapm_client):
    waiting_httpsserver.serve_content(code=202, content="", headers={"Location": "http://example.com/foo"})
    transport = Transport(waiting_httpsserver.url, client=elasticapm_client)
    try:
        with pytest.raises(TransportException) as exc_info:
            url = transport.send(compat.b("x"))
        assert "CERTIFICATE_VERIFY_FAILED" in str(exc_info)
    finally:
        transport.close()


@pytest.mark.filterwarnings("ignore:Unverified HTTPS")
def test_ssl_verify_disable(waiting_httpsserver, elasticapm_client):
    waiting_httpsserver.serve_content(code=202, content="", headers={"Location": "https://example.com/foo"})
    transport = Transport(waiting_httpsserver.url, verify_server_cert=False, client=elasticapm_client)
    try:
        url = transport.send(compat.b("x"))
        assert url == "https://example.com/foo"
    finally:
        transport.close()


def test_ssl_verify_disable_http(waiting_httpserver, elasticapm_client):
    """
    Make sure that ``assert_hostname`` isn't passed in for http requests, even
    with verify_server_cert=False
    """
    waiting_httpserver.serve_content(code=202, content="", headers={"Location": "http://example.com/foo"})
    transport = Transport(waiting_httpserver.url, verify_server_cert=False, client=elasticapm_client)
    try:
        url = transport.send(compat.b("x"))
        assert url == "http://example.com/foo"
    finally:
        transport.close()


def test_ssl_cert_pinning_http(waiting_httpserver, elasticapm_client):
    """
    Won't fail, as with the other cert pinning test, since certs aren't relevant
    for http, only https.
    """
    waiting_httpserver.serve_content(code=202, content="", headers={"Location": "http://example.com/foo"})
    transport = Transport(
        waiting_httpserver.url,
        server_cert=os.path.join(os.path.dirname(__file__), "wrong_cert.pem"),
        verify_server_cert=True,
        client=elasticapm_client,
    )
    try:
        url = transport.send(compat.b("x"))
        assert url == "http://example.com/foo"
    finally:
        transport.close()


def test_ssl_cert_pinning(waiting_httpsserver, elasticapm_client):
    waiting_httpsserver.serve_content(code=202, content="", headers={"Location": "https://example.com/foo"})
    cur_dir = os.path.dirname(os.path.realpath(__file__))
    transport = Transport(
        waiting_httpsserver.url,
        server_cert=os.path.join(cur_dir, "..", "ca/server.pem"),
        verify_server_cert=True,
        client=elasticapm_client,
    )
    try:
        url = transport.send(compat.b("x"))
        assert url == "https://example.com/foo"
    finally:
        transport.close()


def test_ssl_cert_pinning_fails(waiting_httpsserver, elasticapm_client):
    if compat.PY3:
        waiting_httpsserver.serve_content(code=202, content="", headers={"Location": "https://example.com/foo"})
        url = waiting_httpsserver.url
    else:
        # if we use the local test server here, execution blocks somewhere deep in OpenSSL on Python 2.7, presumably
        # due to a threading issue that has been fixed in later versions. To avoid that, we have to commit a minor
        # cardinal sin here and do an outside request to https://example.com (which will also fail the fingerprint
        # assertion).
        #
        # May the Testing Goat have mercy on our souls.
        url = "https://example.com"
    transport = Transport(
        url,
        server_cert=os.path.join(os.path.dirname(__file__), "wrong_cert.pem"),
        verify_server_cert=True,
        client=elasticapm_client,
    )
    try:
        with pytest.raises(TransportException) as exc_info:
            transport.send(compat.b("x"))
    finally:
        transport.close()

    assert "Fingerprints did not match" in exc_info.value.args[0]


def test_config_url():
    transport = Transport("http://example.com/" + constants.EVENTS_API_PATH, client=None)
    try:
        assert transport._config_url == "http://example.com/" + constants.AGENT_CONFIG_PATH
    finally:
        transport.close()


def test_get_config(waiting_httpserver, elasticapm_client):
    waiting_httpserver.serve_content(
        code=200, content=b'{"x": "y"}', headers={"Cache-Control": "max-age=5", "Etag": "2"}
    )
    url = waiting_httpserver.url
    transport = Transport(url + "/" + constants.EVENTS_API_PATH, client=elasticapm_client)
    try:
        version, data, max_age = transport.get_config("1", {})
        assert version == "2"
        assert data == {"x": "y"}
        assert max_age == 5
    finally:
        transport.close()


@mock.patch("urllib3.poolmanager.PoolManager.urlopen")
def test_get_config_handle_exception(mock_urlopen, caplog, elasticapm_client):
    transport = Transport("http://example.com/" + constants.EVENTS_API_PATH, client=elasticapm_client)
    mock_urlopen.side_effect = urllib3.exceptions.RequestError(transport.http, "http://example.com/", "boom")
    try:
        with caplog.at_level("DEBUG", "elasticapm.transport.http"):
            version, data, max_age = transport.get_config("1", {})
        assert version == "1"
        assert max_age == 300
        record = caplog.records[-1]
        assert "HTTP error" in record.msg
    finally:
        transport.close()


def test_get_config_cache_headers_304(waiting_httpserver, caplog, elasticapm_client):
    waiting_httpserver.serve_content(code=304, content=b"", headers={"Cache-Control": "max-age=5"})
    url = waiting_httpserver.url
    transport = Transport(url + "/" + constants.EVENTS_API_PATH, client=elasticapm_client)
    try:
        with caplog.at_level("DEBUG", "elasticapm.transport.http"):
            version, data, max_age = transport.get_config("1", {})
        assert waiting_httpserver.requests[0].headers["If-None-Match"] == "1"
        assert version == "1"
        assert data is None
        assert max_age == 5
        record = caplog.records[-1]
        assert "Configuration unchanged" in record.msg
    finally:
        transport.close()


def test_get_config_bad_cache_control_header(waiting_httpserver, caplog, elasticapm_client):
    waiting_httpserver.serve_content(
        code=200, content=b'{"x": "y"}', headers={"Cache-Control": "max-age=fifty", "Etag": "2"}
    )
    url = waiting_httpserver.url
    transport = Transport(url + "/" + constants.EVENTS_API_PATH, client=elasticapm_client)
    try:
        with caplog.at_level("DEBUG", "elasticapm.transport.http"):
            version, data, max_age = transport.get_config("1", {})
        assert version == "2"
        assert data == {"x": "y"}
        assert max_age == 300
        record = caplog.records[-1]
        assert record.message == "Could not parse Cache-Control header: max-age=fifty"
    finally:
        transport.close()


def test_get_config_empty_response(waiting_httpserver, caplog, elasticapm_client):
    waiting_httpserver.serve_content(code=200, content=b"", headers={"Cache-Control": "max-age=5"})
    url = waiting_httpserver.url
    transport = Transport(url + "/" + constants.EVENTS_API_PATH, client=elasticapm_client)
    try:
        with caplog.at_level("DEBUG", "elasticapm.transport.http"):
            version, data, max_age = transport.get_config("1", {})
        assert version == "1"
        assert data is None
        assert max_age == 5
        record = caplog.records[-1]
        assert record.message == "APM Server answered with empty body and status code 200"
    finally:
        transport.close()
