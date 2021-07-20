# -*- coding: utf-8 -*-

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

from __future__ import absolute_import

import os
import platform
import socket
import sys
import time
import warnings
from collections import defaultdict

import mock
import pytest
from pytest_localserver.http import ContentServer
from pytest_localserver.https import DEFAULT_CERTIFICATE

import elasticapm
from elasticapm.base import Client
from elasticapm.conf.constants import ERROR, KEYWORD_MAX_LENGTH, SPAN, TRANSACTION
from elasticapm.traces import transaction
from elasticapm.utils import compat, encoding
from elasticapm.utils.disttracing import TraceParent
from tests.fixtures import DummyTransport, TempStoreClient
from tests.utils import assert_any_record_contains


@pytest.mark.parametrize("elasticapm_client", [{"environment": "production"}], indirect=True)
def test_service_info(elasticapm_client):
    service_info = elasticapm_client.get_service_info()
    assert service_info["name"] == elasticapm_client.config.service_name
    assert service_info["environment"] == elasticapm_client.config.environment == "production"
    assert service_info["language"] == {"name": "python", "version": platform.python_version()}
    assert service_info["agent"]["name"] == "python"


@pytest.mark.parametrize(
    "elasticapm_client", [{"environment": "production", "service_node_name": "my_node"}], indirect=True
)
def test_service_info_node_name(elasticapm_client):
    service_info = elasticapm_client.get_service_info()
    assert service_info["name"] == elasticapm_client.config.service_name
    assert service_info["environment"] == elasticapm_client.config.environment == "production"
    assert service_info["language"] == {"name": "python", "version": platform.python_version()}
    assert service_info["agent"]["name"] == "python"
    assert service_info["node"]["configured_name"] == "my_node"


def test_process_info(elasticapm_client):
    with mock.patch.object(sys, "argv", ["a", "b", "c"]):
        process_info = elasticapm_client.get_process_info()
    assert process_info["pid"] == os.getpid()
    if hasattr(os, "getppid"):
        assert process_info["ppid"] == os.getppid()
    else:
        # Windows + Python 2.7
        assert process_info["ppid"] is None
    assert process_info["argv"] == ["a", "b", "c"]


def test_system_info(elasticapm_client):
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    with mock.patch("elasticapm.utils.cgroup.get_cgroup_container_metadata") as mocked:
        mocked.return_value = {}
        system_info = elasticapm_client.get_system_info()
    assert {"hostname", "architecture", "platform"} == set(system_info.keys())
    assert system_info["hostname"] == socket.gethostname()


@pytest.mark.parametrize("elasticapm_client", [{"hostname": "my_custom_hostname"}], indirect=True)
def test_system_info_hostname_configurable(elasticapm_client):
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    system_info = elasticapm_client.get_system_info()
    assert system_info["hostname"] == "my_custom_hostname"


@pytest.mark.parametrize("elasticapm_client", [{"global_labels": "az=us-east-1,az.rack=8"}], indirect=True)
def test_global_labels(elasticapm_client):
    data = elasticapm_client.build_metadata()
    assert data["labels"] == {"az": "us-east-1", "az_rack": "8"}


def test_docker_kubernetes_system_info(elasticapm_client):
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    with mock.patch("elasticapm.utils.cgroup.get_cgroup_container_metadata") as mock_metadata:
        mock_metadata.return_value = {"container": {"id": "123"}, "kubernetes": {"pod": {"uid": "456"}}}
        system_info = elasticapm_client.get_system_info()
    assert system_info["container"] == {"id": "123"}
    assert system_info["kubernetes"] == {"pod": {"uid": "456", "name": socket.gethostname()}}


@mock.patch.dict(
    "os.environ",
    {
        "KUBERNETES_NODE_NAME": "node",
        "KUBERNETES_NAMESPACE": "namespace",
        "KUBERNETES_POD_NAME": "pod",
        "KUBERNETES_POD_UID": "podid",
    },
)
def test_docker_kubernetes_system_info_from_environ():
    # initialize agent only after overriding environment
    elasticapm_client = TempStoreClient(metrics_interval="0ms")
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    with mock.patch("elasticapm.utils.cgroup.get_cgroup_container_metadata") as mock_metadata:
        mock_metadata.return_value = {}
        system_info = elasticapm_client.get_system_info()
    assert "kubernetes" in system_info
    assert system_info["kubernetes"] == {
        "pod": {"uid": "podid", "name": "pod"},
        "node": {"name": "node"},
        "namespace": "namespace",
    }


@mock.patch.dict(
    "os.environ",
    {
        "KUBERNETES_NODE_NAME": "node",
        "KUBERNETES_NAMESPACE": "namespace",
        "KUBERNETES_POD_NAME": "pod",
        "KUBERNETES_POD_UID": "podid",
    },
)
def test_docker_kubernetes_system_info_from_environ_overrides_cgroups():
    # initialize agent only after overriding environment
    elasticapm_client = TempStoreClient(metrics_interval="0ms")
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    with mock.patch("elasticapm.utils.cgroup.get_cgroup_container_metadata") as mock_metadata, mock.patch(
        "socket.gethostname"
    ) as mock_gethostname:
        mock_metadata.return_value = {"container": {"id": "123"}, "kubernetes": {"pod": {"uid": "456"}}}
        mock_gethostname.return_value = "foo"
        system_info = elasticapm_client.get_system_info()
    assert "kubernetes" in system_info

    assert system_info["kubernetes"] == {
        "pod": {"uid": "podid", "name": "pod"},
        "node": {"name": "node"},
        "namespace": "namespace",
    }
    assert system_info["container"] == {"id": "123"}


@mock.patch.dict("os.environ", {"KUBERNETES_NAMESPACE": "namespace"})
def test_docker_kubernetes_system_info_except_hostname_from_environ():
    # initialize agent only after overriding environment
    elasticapm_client = TempStoreClient(metrics_interval="0ms")
    # mock docker/kubernetes data here to get consistent behavior if test is run in docker
    with mock.patch("elasticapm.utils.cgroup.get_cgroup_container_metadata") as mock_metadata, mock.patch(
        "socket.gethostname"
    ) as mock_gethostname:
        mock_metadata.return_value = {}
        mock_gethostname.return_value = "foo"
        system_info = elasticapm_client.get_system_info()
    assert "kubernetes" in system_info
    assert system_info["kubernetes"] == {"pod": {"name": socket.gethostname()}, "namespace": "namespace"}


def test_config_by_environment():
    with mock.patch.dict("os.environ", {"ELASTIC_APM_SERVICE_NAME": "envapp", "ELASTIC_APM_SECRET_TOKEN": "envtoken"}):
        client = TempStoreClient(metrics_interval="0ms")
        assert client.config.service_name == "envapp"
        assert client.config.secret_token == "envtoken"
        assert client.config.disable_send is False
    with mock.patch.dict("os.environ", {"ELASTIC_APM_DISABLE_SEND": "true"}):
        client = TempStoreClient(metrics_interval="0ms")
        assert client.config.disable_send is True
    client.close()


def test_config_non_string_types():
    """
    tests if we can handle non string types as configuration, e.g.
    Value types from django-configuration
    """

    class MyValue(object):
        def __init__(self, content):
            self.content = content

        def __str__(self):
            return str(self.content)

        def __repr__(self):
            return repr(self.content)

    client = TempStoreClient(
        server_url="localhost", service_name=MyValue("bar"), secret_token=MyValue("bay"), metrics_interval="0ms"
    )
    assert isinstance(client.config.secret_token, compat.string_types)
    assert isinstance(client.config.service_name, compat.string_types)
    client.close()


@pytest.mark.parametrize("elasticapm_client", [{"transport_class": "tests.fixtures.DummyTransport"}], indirect=True)
def test_custom_transport(elasticapm_client):
    assert isinstance(elasticapm_client._transport, DummyTransport)


@pytest.mark.parametrize("elasticapm_client", [{"processors": []}], indirect=True)
def test_empty_processor_list(elasticapm_client):
    assert elasticapm_client.processors == []


@pytest.mark.flaky(reruns=3)  # test is flaky on Windows
@pytest.mark.parametrize(
    "sending_elasticapm_client",
    [{"transport_class": "elasticapm.transport.http.Transport", "async_mode": False}],
    indirect=True,
)
@pytest.mark.parametrize("validating_httpserver", [{"app": ContentServer}], indirect=True)
@mock.patch("elasticapm.transport.base.TransportState.should_try")
def test_send_remote_failover_sync(should_try, sending_elasticapm_client, caplog):
    sending_elasticapm_client.httpserver.code = 400
    sending_elasticapm_client.httpserver.content = "go away"
    should_try.return_value = True

    # test error
    with caplog.at_level("ERROR", "elasticapm.transport"):
        sending_elasticapm_client.capture_message("foo", handled=False)
    sending_elasticapm_client._transport.flush()
    assert sending_elasticapm_client._transport.state.did_fail()
    assert_any_record_contains(caplog.records, "go away")

    # test recovery
    sending_elasticapm_client.httpserver.code = 202
    sending_elasticapm_client.capture_message("bar", handled=False)
    sending_elasticapm_client.close()
    assert not sending_elasticapm_client._transport.state.did_fail()


@mock.patch("elasticapm.transport.http.Transport.send")
@mock.patch("elasticapm.transport.base.TransportState.should_try")
def test_send_remote_failover_sync_non_transport_exception_error(should_try, http_send, caplog):
    should_try.return_value = True

    client = Client(
        server_url="http://example.com",
        service_name="app_name",
        secret_token="secret",
        transport_class="elasticapm.transport.http.Transport",
        metrics_interval="0ms",
        metrics_sets=[],
    )
    # test error
    http_send.side_effect = ValueError("oopsie")
    with caplog.at_level("ERROR", "elasticapm.transport"):
        client.capture_message("foo", handled=False)
    client._transport.flush()
    assert client._transport.state.did_fail()
    assert_any_record_contains(caplog.records, "oopsie", "elasticapm.transport")

    # test recovery
    http_send.side_effect = None
    client.capture_message("foo", handled=False)
    client.close()
    assert not client._transport.state.did_fail()
    client.close()


@pytest.mark.parametrize("validating_httpserver", [{"skip_validate": True}], indirect=True)
def test_send(sending_elasticapm_client):
    sending_elasticapm_client.queue("x", {})
    sending_elasticapm_client.close()
    request = sending_elasticapm_client.httpserver.requests[0]
    expected_headers = {
        "Content-Type": "application/x-ndjson",
        "Content-Encoding": "gzip",
        "Authorization": "Bearer %s" % sending_elasticapm_client.config.secret_token,
        "User-Agent": "elasticapm-python/%s" % elasticapm.VERSION,
    }
    seen_headers = dict(request.headers)
    for k, v in expected_headers.items():
        assert seen_headers[k] == v

    # Commented out per @beniwohli
    # TODO: figure out why payload size is larger than 400 on windows / 2.7
    # assert 250 < request.content_length < 400


@pytest.mark.flaky(reruns=3)  # test is flaky on Windows
@pytest.mark.parametrize("sending_elasticapm_client", [{"disable_send": True}], indirect=True)
def test_send_not_enabled(sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send
    sending_elasticapm_client.queue("x", {})
    sending_elasticapm_client.close()

    assert len(sending_elasticapm_client.httpserver.requests) == 0


@pytest.mark.parametrize(
    "sending_elasticapm_client",
    [{"transport_class": "elasticapm.transport.http.Transport", "async_mode": False}],
    indirect=True,
)
def test_client_shutdown_sync(sending_elasticapm_client):
    sending_elasticapm_client.capture_message("x")
    sending_elasticapm_client.close()
    assert len(sending_elasticapm_client.httpserver.requests) == 1


def test_call_end_twice(elasticapm_client):
    elasticapm_client.begin_transaction("celery")

    elasticapm_client.end_transaction("test-transaction", 200)
    elasticapm_client.end_transaction("test-transaction", 200)


@pytest.mark.parametrize("elasticapm_client", [{"verify_server_cert": False}], indirect=True)
def test_client_disables_ssl_verification(elasticapm_client):
    assert not elasticapm_client.config.verify_server_cert
    assert not elasticapm_client._transport._verify_server_cert


@pytest.mark.parametrize("sending_elasticapm_client", [{"server_cert": DEFAULT_CERTIFICATE}], indirect=True)
def test_server_cert_pinning(sending_elasticapm_client):
    assert sending_elasticapm_client._transport._server_cert == DEFAULT_CERTIFICATE


@pytest.mark.parametrize(
    "elasticapm_client", [{"transactions_ignore_patterns": ["^OPTIONS", "views.api.v2"]}], indirect=True
)
def test_ignore_patterns(elasticapm_client):
    elasticapm_client.begin_transaction("web")
    elasticapm_client.end_transaction("OPTIONS views.healthcheck", 200)

    elasticapm_client.begin_transaction("web")
    elasticapm_client.end_transaction("GET views.users", 200)

    transactions = elasticapm_client.events[TRANSACTION]

    assert len(transactions) == 1
    assert transactions[0]["name"] == "GET views.users"


@pytest.mark.parametrize(
    "elasticapm_client", [{"transactions_ignore_patterns": ["^OPTIONS", "views.api.v2"]}], indirect=True
)
def test_ignore_patterns_with_none_transaction_name(elasticapm_client):
    elasticapm_client.begin_transaction("web")
    t = elasticapm_client.end_transaction(None, 200)
    assert t.name == ""


@pytest.mark.parametrize(
    "setting,url,result",
    [
        ("", "/foo/bar", False),
        ("*", "/foo/bar", True),
        ("/foo/bar,/baz", "/foo/bar", True),
        ("/foo/bar,/baz", "/baz", True),
        ("/foo/bar,/bazz", "/baz", False),
        ("/foo/*/bar,/bazz", "/foo/bar", False),
        ("/foo/*/bar,/bazz", "/foo/ooo/bar", True),
        ("*/foo/*/bar,/bazz", "/foo/ooo/bar", True),
        ("*/foo/*/bar,/bazz", "/baz/foo/ooo/bar", True),
    ],
)
def test_should_ignore_url(elasticapm_client, setting, url, result):
    elasticapm_client.config.update(1, transaction_ignore_urls=setting)
    assert elasticapm_client.should_ignore_url(url) is result


@pytest.mark.parametrize("sending_elasticapm_client", [{"disable_send": True}], indirect=True)
def test_disable_send(sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send

    sending_elasticapm_client.capture("Message", message="test", data={"logger": "test"})

    assert len(sending_elasticapm_client.httpserver.requests) == 0


@pytest.mark.parametrize("elasticapm_client", [{"service_name": "@%&!"}], indirect=True)
def test_invalid_service_name_disables_send(elasticapm_client):
    assert len(elasticapm_client.config.errors) == 1
    assert "SERVICE_NAME" in elasticapm_client.config.errors

    assert elasticapm_client.config.disable_send


def test_empty_transport_disables_send():
    client = TempStoreClient(service_name="x", transport_class=None, metrics_interval="0ms")
    assert len(client.config.errors) == 1
    assert "TRANSPORT_CLASS" in client.config.errors

    assert client.config.disable_send
    client.close()


@pytest.mark.parametrize(
    "elasticapm_client",
    [
        {"collect_local_variables": "errors"},
        {"collect_local_variables": "transactions", "local_var_max_length": 20, "local_var_max_list_length": 10},
        {"collect_local_variables": "all", "local_var_max_length": 20, "local_var_max_list_length": 10},
        {"collect_local_variables": "something"},
    ],
    indirect=True,
)
def test_collect_local_variables_transactions(elasticapm_client):
    mode = elasticapm_client.config.collect_local_variables
    elasticapm_client.begin_transaction("test")
    with elasticapm.capture_span("foo"):
        a_local_var = 1
        a_long_local_var = 100 * "a"
        a_long_local_list = list(range(100))
        pass
    elasticapm_client.end_transaction("test", "ok")
    frame = elasticapm_client.events[SPAN][0]["stacktrace"][0]
    if mode in ("transactions", "all"):
        assert "vars" in frame, mode
        assert frame["vars"]["a_local_var"] == 1
        assert len(frame["vars"]["a_long_local_var"]) == 20
        assert len(frame["vars"]["a_long_local_list"]) == 12
        assert frame["vars"]["a_long_local_list"][-1] == "(90 more elements)"
    else:
        assert "vars" not in frame, mode


@pytest.mark.parametrize(
    "elasticapm_client",
    [
        {"source_lines_span_library_frames": 0, "source_lines_span_app_frames": 0},
        {"source_lines_span_library_frames": 1, "source_lines_span_app_frames": 1},
        {"source_lines_span_library_frames": 7, "source_lines_span_app_frames": 5},
    ],
    indirect=True,
)
def test_collect_source_transactions(elasticapm_client):
    library_frame_context = elasticapm_client.config.source_lines_span_library_frames
    in_app_frame_context = elasticapm_client.config.source_lines_span_app_frames
    elasticapm_client.begin_transaction("test")
    with elasticapm.capture_span("foo"):
        pass
    elasticapm_client.end_transaction("test", "ok")
    span = elasticapm_client.events[SPAN][0]
    in_app_frame = span["stacktrace"][0]
    library_frame = span["stacktrace"][1]
    assert not in_app_frame["library_frame"]
    assert library_frame["library_frame"]
    if library_frame_context:
        assert "context_line" in library_frame, library_frame_context
        assert "pre_context" in library_frame, library_frame_context
        assert "post_context" in library_frame, library_frame_context
        lines = len([library_frame["context_line"]] + library_frame["pre_context"] + library_frame["post_context"])
        assert lines == library_frame_context, library_frame_context
    else:
        assert "context_line" not in library_frame, library_frame_context
        assert "pre_context" not in library_frame, library_frame_context
        assert "post_context" not in library_frame, library_frame_context
    if in_app_frame_context:
        assert "context_line" in in_app_frame, in_app_frame_context
        assert "pre_context" in in_app_frame, in_app_frame_context
        assert "post_context" in in_app_frame, in_app_frame_context
        lines = len([in_app_frame["context_line"]] + in_app_frame["pre_context"] + in_app_frame["post_context"])
        assert lines == in_app_frame_context, (in_app_frame_context, in_app_frame["lineno"])
    else:
        assert "context_line" not in in_app_frame, in_app_frame_context
        assert "pre_context" not in in_app_frame, in_app_frame_context
        assert "post_context" not in in_app_frame, in_app_frame_context


@pytest.mark.parametrize("elasticapm_client", [{"transaction_sample_rate": 0.4}], indirect=True)
def test_transaction_sampling(elasticapm_client, not_so_random):
    for i in range(10):
        elasticapm_client.begin_transaction("test_type")
        with elasticapm.capture_span("xyz"):
            pass
        elasticapm_client.end_transaction("test")

    transactions = elasticapm_client.events[TRANSACTION]
    spans_per_transaction = defaultdict(list)
    for span in elasticapm_client.events[SPAN]:
        spans_per_transaction[span["transaction_id"]].append(span)

    # seed is fixed by not_so_random fixture
    assert len([t for t in transactions if t["sampled"]]) == 3
    for transaction in transactions:
        assert transaction["sampled"] or not transaction["id"] in spans_per_transaction
        assert transaction["sampled"] or not "context" in transaction
        assert transaction["sample_rate"] == 0 if not transaction["sampled"] else transaction["sample_rate"] == 0.4


def test_transaction_sample_rate_dynamic(elasticapm_client, not_so_random):
    elasticapm_client.config.update(version="1", transaction_sample_rate=0.4)
    for i in range(10):
        elasticapm_client.begin_transaction("test_type")
        with elasticapm.capture_span("xyz"):
            pass
        elasticapm_client.end_transaction("test")

    transactions = elasticapm_client.events[TRANSACTION]
    spans_per_transaction = defaultdict(list)
    for span in elasticapm_client.events[SPAN]:
        spans_per_transaction[span["transaction_id"]].append(span)

    # seed is fixed by not_so_random fixture
    assert len([t for t in transactions if t["sampled"]]) == 3
    for transaction in transactions:
        assert transaction["sampled"] or not transaction["id"] in spans_per_transaction
        assert transaction["sampled"] or not "context" in transaction

    elasticapm_client.config.update(version="1", transaction_sample_rate=1.0)
    for i in range(5):
        elasticapm_client.begin_transaction("test_type")
        with elasticapm.capture_span("xyz"):
            pass
        elasticapm_client.end_transaction("test")

    transactions = elasticapm_client.events[TRANSACTION]

    # seed is fixed by not_so_random fixture
    assert len([t for t in transactions if t["sampled"]]) == 8


@pytest.mark.parametrize("elasticapm_client", [{"transaction_max_spans": 5}], indirect=True)
def test_transaction_max_spans(elasticapm_client):
    elasticapm_client.begin_transaction("test_type")
    for i in range(5):
        with elasticapm.capture_span("nodrop"):
            pass
    for i in range(10):
        with elasticapm.capture_span("drop"):
            pass
    transaction_obj = elasticapm_client.end_transaction("test")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.events[SPAN]
    assert all(span["transaction_id"] == transaction["id"] for span in spans)

    assert transaction_obj.dropped_spans == 10
    assert len(spans) == 5
    for span in spans:
        assert span["name"] == "nodrop"
    assert transaction["span_count"] == {"dropped": 10, "started": 5}


def test_transaction_max_spans_dynamic(elasticapm_client):
    elasticapm_client.config.update(version=1, transaction_max_spans=1)
    elasticapm_client.begin_transaction("test_type")
    for i in range(5):
        with elasticapm.capture_span("span"):
            pass
    elasticapm_client.end_transaction("test")
    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1

    elasticapm_client.config.update(version=2, transaction_max_spans=3)
    elasticapm_client.begin_transaction("test_type")
    for i in range(5):
        with elasticapm.capture_span("span"):
            pass

    elasticapm_client.end_transaction("test")
    transaction = elasticapm_client.events[TRANSACTION][1]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 3


@pytest.mark.parametrize("elasticapm_client", [{"span_frames_min_duration": 20}], indirect=True)
def test_transaction_span_frames_min_duration(elasticapm_client):
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("noframes", duration=0.001):
        pass
    with elasticapm.capture_span("frames", duration=0.04):
        pass
    elasticapm_client.end_transaction("test")

    spans = elasticapm_client.events[SPAN]

    assert len(spans) == 2
    assert spans[0]["name"] == "noframes"
    assert "stacktrace" not in spans[0]

    assert spans[1]["name"] == "frames"
    assert spans[1]["stacktrace"] is not None


@pytest.mark.parametrize("elasticapm_client", [{"span_frames_min_durarion_ms": -1}], indirect=True)
def test_transaction_span_frames_min_duration_no_limit(elasticapm_client):
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("frames"):
        pass
    with elasticapm.capture_span("frames", duration=0.04):
        pass
    elasticapm_client.end_transaction("test")

    spans = elasticapm_client.events[SPAN]

    assert len(spans) == 2
    assert spans[0]["name"] == "frames"
    assert spans[0]["stacktrace"] is not None

    assert spans[1]["name"] == "frames"
    assert spans[1]["stacktrace"] is not None


def test_transaction_span_frames_min_duration_dynamic(elasticapm_client):
    elasticapm_client.config.update(version="1", span_frames_min_duration=20)
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("noframes", duration=0.001):
        pass
    with elasticapm.capture_span("frames", duration=0.04):
        pass
    elasticapm_client.end_transaction("test")

    spans = elasticapm_client.events[SPAN]

    assert len(spans) == 2
    assert spans[0]["name"] == "noframes"
    assert "stacktrace" not in spans[0]

    assert spans[1]["name"] == "frames"
    assert spans[1]["stacktrace"] is not None

    elasticapm_client.config.update(version="1", span_frames_min_duration=-1)
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("frames"):
        pass
    with elasticapm.capture_span("frames", duration=0.04):
        pass
    elasticapm_client.end_transaction("test")

    spans = elasticapm_client.events[SPAN]

    assert len(spans) == 4
    assert spans[2]["name"] == "frames"
    assert spans[2]["stacktrace"] is not None

    assert spans[3]["name"] == "frames"
    assert spans[3]["stacktrace"] is not None


@pytest.mark.parametrize("elasticapm_client", [{"transaction_max_spans": 3}], indirect=True)
def test_transaction_max_span_nested(elasticapm_client):
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("1"):
        with elasticapm.capture_span("2"):
            with elasticapm.capture_span("3"):
                with elasticapm.capture_span("4"):
                    with elasticapm.capture_span("5"):
                        pass
                with elasticapm.capture_span("6"):
                    pass
            with elasticapm.capture_span("7"):
                pass
        with elasticapm.capture_span("8"):
            pass
    with elasticapm.capture_span("9"):
        pass
    transaction_obj = elasticapm_client.end_transaction("test")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.events[SPAN]

    assert transaction_obj.dropped_spans == 6
    assert len(spans) == 3
    for span in spans:
        assert span["name"] in ("1", "2", "3")
    assert transaction["span_count"] == {"dropped": 6, "started": 3}


def test_transaction_keyword_truncation(elasticapm_client):
    too_long = "x" * (KEYWORD_MAX_LENGTH + 1)
    expected = encoding.keyword_field(too_long)
    assert too_long != expected
    assert len(expected) == KEYWORD_MAX_LENGTH
    assert expected[-1] != "x"
    elasticapm_client.begin_transaction(too_long)
    elasticapm.label(val=too_long)
    elasticapm.set_user_context(username=too_long, email=too_long, user_id=too_long)
    with elasticapm.capture_span(name=too_long, span_type=too_long):
        pass
    elasticapm_client.end_transaction(too_long, too_long)
    elasticapm_client.close()

    span = elasticapm_client.events["span"][0]
    transaction = elasticapm_client.events["transaction"][0]

    assert transaction["name"] == expected
    assert transaction["type"] == expected
    assert transaction["result"] == expected

    assert transaction["context"]["user"]["id"] == expected
    assert transaction["context"]["user"]["username"] == expected
    assert transaction["context"]["user"]["email"] == expected

    assert transaction["context"]["tags"]["val"] == expected

    assert span["type"] == expected
    assert span["name"] == expected


@pytest.mark.parametrize("sending_elasticapm_client", [{"service_name": "*"}], indirect=True)
@mock.patch("elasticapm.transport.base.Transport.send")
def test_config_error_stops_error_send(mock_send, sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send is True
    sending_elasticapm_client.capture_message("bla", handled=False)
    assert mock_send.call_count == 0


@pytest.mark.parametrize("sending_elasticapm_client", [{"service_name": "*"}], indirect=True)
@mock.patch("elasticapm.transport.base.Transport.send")
def test_config_error_stops_transaction_send(mock_send, sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send is True
    sending_elasticapm_client.begin_transaction("test")
    sending_elasticapm_client.end_transaction("test", "OK")
    sending_elasticapm_client.close()
    assert mock_send.call_count == 0


def test_trace_parent(elasticapm_client):
    trace_parent = TraceParent.from_string("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03")
    elasticapm_client.begin_transaction("test", trace_parent=trace_parent)
    transaction = elasticapm_client.end_transaction("test", "OK")
    data = transaction.to_dict()
    assert data["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    assert data["parent_id"] == "b7ad6b7169203331"


def test_sample_rate_in_dict(elasticapm_client):
    trace_parent = TraceParent.from_string(
        "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03", tracestate_string="es=s:0.43"
    )
    elasticapm_client.begin_transaction("test", trace_parent=trace_parent)
    with elasticapm.capture_span("x"):
        pass
    transaction = elasticapm_client.end_transaction("test", "OK")
    data = transaction.to_dict()
    assert data["sample_rate"] == 0.43
    assert elasticapm_client.events[SPAN][0]["sample_rate"] == 0.43


def test_sample_rate_undefined_by_parent(elasticapm_client):
    trace_parent = TraceParent.from_string("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-03")
    elasticapm_client.begin_transaction("test", trace_parent=trace_parent)
    with elasticapm.capture_span("x"):
        pass
    transaction = elasticapm_client.end_transaction("test", "OK")
    data = transaction.to_dict()
    assert "sample_rate" not in data
    assert "sample_rate" not in elasticapm_client.events[SPAN][0]


def test_trace_parent_not_set(elasticapm_client):
    elasticapm_client.begin_transaction("test", trace_parent=None)
    transaction = elasticapm_client.end_transaction("test", "OK")
    data = transaction.to_dict()
    assert data["trace_id"] is not None
    assert "parent_id" not in data


def test_ensure_parent_sets_new_id(elasticapm_client):
    transaction = elasticapm_client.begin_transaction("test", trace_parent=None)
    assert transaction.id == transaction.trace_parent.span_id
    span_id = transaction.ensure_parent_id()
    assert span_id == transaction.trace_parent.span_id


def test_ensure_parent_doesnt_change_existing_id(elasticapm_client):
    transaction = elasticapm_client.begin_transaction("test", trace_parent=None)
    assert transaction.id == transaction.trace_parent.span_id
    span_id = transaction.ensure_parent_id()
    span_id_2 = transaction.ensure_parent_id()
    assert span_id == span_id_2


@pytest.mark.parametrize(
    "elasticapm_client,expected",
    [
        ({"server_url": "http://localhost"}, "http://localhost/intake/v2/events"),
        ({"server_url": "http://localhost/"}, "http://localhost/intake/v2/events"),
        ({"server_url": "http://localhost:8200"}, "http://localhost:8200/intake/v2/events"),
        ({"server_url": "http://localhost:8200/"}, "http://localhost:8200/intake/v2/events"),
        ({"server_url": "http://localhost/a"}, "http://localhost/a/intake/v2/events"),
        ({"server_url": "http://localhost/a/"}, "http://localhost/a/intake/v2/events"),
        ({"server_url": "http://localhost:8200/a"}, "http://localhost:8200/a/intake/v2/events"),
        ({"server_url": "http://localhost:8200/a/"}, "http://localhost:8200/a/intake/v2/events"),
    ],
    indirect=["elasticapm_client"],
)
def test_server_url_joining(elasticapm_client, expected):
    assert elasticapm_client._api_endpoint_url == expected


@pytest.mark.parametrize(
    "version,raises,pending",
    [
        (("2", "7", "0"), True, True),
        (("3", "3", "0"), True, False),
        (("3", "4", "0"), True, False),
        (("3", "5", "0"), False, False),
    ],
)
@mock.patch("platform.python_version_tuple")
def test_python_version_deprecation(mock_python_version_tuple, version, raises, pending, recwarn):
    warnings.simplefilter("always")

    mock_python_version_tuple.return_value = version
    e = None
    try:
        e = elasticapm.Client()
    finally:
        if e:
            e.close()
    if raises:
        if pending:
            w = recwarn.pop(PendingDeprecationWarning)
            assert "will stop supporting" in w.message.args[0]
        else:
            w = recwarn.pop(DeprecationWarning)
            assert "agent only supports" in w.message.args[0]


def test_recording(elasticapm_client):
    assert elasticapm_client.capture_message("x") is not None
    try:
        1 / 0
    except ZeroDivisionError:
        assert elasticapm_client.capture_exception() is not None
    assert elasticapm_client.begin_transaction("test") is not None
    with elasticapm.capture_span("x") as x_span:
        assert x_span is not None
    assert elasticapm_client.end_transaction("ok", "ok") is not None

    elasticapm_client.config.update("1", recording=False)
    assert not elasticapm_client.config.is_recording
    assert elasticapm_client.capture_message("x") is None
    try:
        1 / 0
    except ZeroDivisionError:
        assert elasticapm_client.capture_exception() is None
    assert elasticapm_client.begin_transaction("test") is None
    with elasticapm.capture_span("x") as x_span:
        assert x_span is None
    assert elasticapm_client.end_transaction("ok", "ok") is None


@pytest.mark.parametrize(
    "elasticapm_client",
    [
        {"enabled": True, "metrics_interval": "30s", "central_config": "true"},
        {"enabled": False, "metrics_interval": "30s", "central_config": "true"},
    ],
    indirect=True,
)
def test_client_enabled(elasticapm_client):
    if elasticapm_client.config.enabled:
        assert elasticapm_client.config.is_recording
        for manager in elasticapm_client._thread_managers.values():
            assert manager.is_started()
    else:
        assert not elasticapm_client.config.is_recording
        for manager in elasticapm_client._thread_managers.values():
            assert not manager.is_started()


def test_excepthook(elasticapm_client):
    try:
        raise Exception("hi!")
    except Exception:
        type_, value, traceback = sys.exc_info()
        elasticapm_client._excepthook(type_, value, traceback)

    assert elasticapm_client.events[ERROR]


def test_transaction_as_decorator(elasticapm_client):
    @elasticapm.transaction()
    def decorated_function():
        with elasticapm.capture_span("bla"):
            pass

    decorated_function()
    transaction = elasticapm_client.events["transaction"][0]
    assert transaction["name"] == "tests.client.client_tests.decorated_function"
    assert transaction["type"] == "code"
    assert transaction["outcome"] == "success"
    assert transaction["span_count"]["started"] == 1


def test_transaction_as_context_manager(elasticapm_client):
    with elasticapm.transaction(name="foo"):
        with elasticapm.capture_span("bla"):
            pass
    transaction = elasticapm_client.events["transaction"][0]
    assert transaction["name"] == "foo"
    assert transaction["type"] == "code"
    assert transaction["outcome"] == "success"
    assert transaction["span_count"]["started"] == 1


def test_transaction_context_manager_with_no_client():
    with elasticapm.transaction(name="foo") as t:
        with elasticapm.capture_span("bla"):
            pass
    assert t is None
