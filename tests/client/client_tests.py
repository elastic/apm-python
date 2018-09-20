# -*- coding: utf-8 -*-
import os
import platform
import sys
import time
from collections import defaultdict

import mock
import pytest
from pytest_localserver.http import ContentServer

import elasticapm
from elasticapm.base import Client
from elasticapm.conf.constants import ERROR, KEYWORD_MAX_LENGTH, SPAN, TRANSACTION
from elasticapm.utils import compat, encoding
from tests.fixtures import DummyTransport


@pytest.mark.parametrize("elasticapm_client", [{"environment": "production"}], indirect=True)
def test_service_info(elasticapm_client):
    service_info = elasticapm_client.get_service_info()
    assert service_info["name"] == elasticapm_client.config.service_name
    assert service_info["environment"] == elasticapm_client.config.environment == "production"
    assert service_info["language"] == {"name": "python", "version": platform.python_version()}
    assert service_info["agent"]["name"] == "python"


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
    system_info = elasticapm_client.get_system_info()
    assert {"hostname", "architecture", "platform"} == set(system_info.keys())


def test_config_by_environment():
    with mock.patch.dict("os.environ", {"ELASTIC_APM_SERVICE_NAME": "app", "ELASTIC_APM_SECRET_TOKEN": "token"}):
        client = Client()
        assert client.config.service_name == "app"
        assert client.config.secret_token == "token"
        assert client.config.disable_send is False
    with mock.patch.dict("os.environ", {"ELASTIC_APM_DISABLE_SEND": "true"}):
        client = Client()
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

    client = Client(server_url="localhost", service_name=MyValue("bar"), secret_token=MyValue("bay"))
    assert isinstance(client.config.secret_token, compat.string_types)
    assert isinstance(client.config.service_name, compat.string_types)
    client.close()


@pytest.mark.parametrize("elasticapm_client", [{"transport_class": "tests.fixtures.DummyTransport"}], indirect=True)
def test_custom_transport(elasticapm_client):
    assert isinstance(elasticapm_client._transport, DummyTransport)


@pytest.mark.parametrize("elasticapm_client", [{"processors": []}], indirect=True)
def test_empty_processor_list(elasticapm_client):
    assert elasticapm_client.processors == []


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
    assert sending_elasticapm_client._transport.state.did_fail()
    record = caplog.records[0]
    assert "go away" in record.message

    # test recovery
    sending_elasticapm_client.httpserver.code = 202
    sending_elasticapm_client.capture_message("bar", handled=False)
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
    )
    # test error
    http_send.side_effect = ValueError("oopsie")
    with caplog.at_level("ERROR", "elasticapm.transport"):
        client.capture_message("foo", handled=False)
    record = caplog.records[0]
    assert client._transport.state.did_fail()
    assert "oopsie" in record.message

    # test recovery
    http_send.side_effect = None
    client.capture_message("foo", handled=False)
    assert not client._transport.state.did_fail()
    client.close()


@pytest.mark.parametrize(
    "sending_elasticapm_client",
    [{"transport_class": "elasticapm.transport.http.AsyncTransport", "async_mode": True}],
    indirect=True,
)
@pytest.mark.parametrize("validating_httpserver", [{"app": ContentServer}], indirect=True)
@mock.patch("elasticapm.transport.base.TransportState.should_try")
def test_send_remote_failover_async(should_try, sending_elasticapm_client, caplog):
    should_try.return_value = True
    sending_elasticapm_client.httpserver.code = 400

    # test error
    with caplog.at_level("ERROR", "elasticapm.transport"):
        sending_elasticapm_client.capture_message("foo", handled=False)
        sending_elasticapm_client.close()
    assert sending_elasticapm_client._transport.state.did_fail()
    assert "400" in caplog.records[0].message

    # test recovery
    sending_elasticapm_client.httpserver.code = 202
    with caplog.at_level("ERROR", "elasticapm.transport"):
        sending_elasticapm_client.capture_message("bar", handled=False)
        sending_elasticapm_client.close()
    assert not sending_elasticapm_client._transport.state.did_fail()


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

    assert 250 < request.content_length < 350


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


@pytest.mark.parametrize(
    "sending_elasticapm_client",
    [{"transport_class": "elasticapm.transport.http.AsyncTransport", "async_mode": True}],
    indirect=True,
)
def test_client_shutdown_async(sending_elasticapm_client):
    sending_elasticapm_client.capture_message("x")
    sending_elasticapm_client.close()
    assert len(sending_elasticapm_client.httpserver.requests) == 1


def test_explicit_message_on_exception_event(elasticapm_client):
    try:
        raise ValueError("foo")
    except ValueError:
        elasticapm_client.capture("Exception", message="foobar")

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert event["exception"]["message"] == "foobar"


@pytest.mark.parametrize(
    "elasticapm_client",
    [{"include_paths": ("tests",), "local_var_max_length": 20, "local_var_list_max_length": 10}],
    indirect=True,
)
def test_exception_event(elasticapm_client):
    try:
        a_local_var = 1
        a_long_local_var = 100 * "a"
        a_long_local_list = list(range(100))
        raise ValueError("foo")
    except ValueError:
        elasticapm_client.capture("Exception")

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert "exception" in event
    exc = event["exception"]
    assert exc["message"] == "ValueError: foo"
    assert exc["type"] == "ValueError"
    assert exc["module"] == ValueError.__module__  # this differs in some Python versions
    assert "stacktrace" in exc
    frames = exc["stacktrace"]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["abs_path"], __file__.replace(".pyc" == ".py")
    assert frame["filename"] == os.path.join("tests", "client", "client_tests.py")
    assert frame["module"] == __name__
    assert frame["function"] == "test_exception_event"
    assert not frame["library_frame"]
    assert frame["vars"]["a_local_var"] == 1
    assert len(frame["vars"]["a_long_local_var"]) == 20
    assert len(frame["vars"]["a_long_local_list"]) == 12
    assert frame["vars"]["a_long_local_list"][-1] == "(90 more elements)"
    assert "timestamp" in event
    assert "log" not in event
    # check that only frames from `tests` module are not marked as library frames
    assert all(
        frame["library_frame"] or frame["module"].startswith("tests") for frame in event["exception"]["stacktrace"]
    )


@pytest.mark.parametrize(
    "elasticapm_client",
    [{"include_paths": ("*/tests/*",), "local_var_max_length": 20, "local_var_list_max_length": 10}],
    indirect=True,
)
def test_message_event(elasticapm_client):
    a_local_var = 1
    a_long_local_var = 100 * "a"
    a_long_local_list = list(range(100))
    elasticapm_client.capture("Message", message="test")

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert event["log"]["message"] == "test"
    assert "stacktrace" not in event
    assert "timestamp" in event
    assert "stacktrace" in event["log"]
    # check that only frames from `tests` module are not marked as library frames
    for frame in event["log"]["stacktrace"]:
        assert frame["library_frame"] or frame["module"].startswith(("tests", "__main__")), (
            frame["module"],
            frame["abs_path"],
        )
    frame = event["log"]["stacktrace"][0]
    assert frame["vars"]["a_local_var"] == 1
    assert len(frame["vars"]["a_long_local_var"]) == 20
    assert len(frame["vars"]["a_long_local_list"]) == 12
    assert frame["vars"]["a_long_local_list"][-1] == "(90 more elements)"


def test_param_message_event(elasticapm_client):
    elasticapm_client.capture("Message", param_message={"message": "test %s %d", "params": ("x", 1)})

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert event["log"]["message"] == "test x 1"
    assert event["log"]["param_message"] == "test %s %d"


def test_message_with_percent(elasticapm_client):
    elasticapm_client.capture("Message", message="This works 100% of the time")

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert event["log"]["message"] == "This works 100% of the time"
    assert event["log"]["param_message"] == "This works 100% of the time"


def test_logger(elasticapm_client):
    elasticapm_client.capture("Message", message="test", logger_name="test")

    assert len(elasticapm_client.events) == 1
    event = elasticapm_client.events[ERROR][0]
    assert event["log"]["logger_name"] == "test"
    assert "timestamp" in event


def test_call_end_twice(elasticapm_client):
    elasticapm_client.begin_transaction("celery")

    elasticapm_client.end_transaction("test-transaction", 200)
    elasticapm_client.end_transaction("test-transaction", 200)


@mock.patch("elasticapm.transport.base.Transport.queue")
@mock.patch("elasticapm.base.is_master_process")
def test_client_doesnt_flush_when_in_master_process(is_master_process, mock_queue):
    # when in the master process, the client should not flush the
    # HTTP transport
    is_master_process.return_value = True
    client = Client(server_url="http://example.com", service_name="app_name", secret_token="secret")
    client.queue("x", {}, flush=True)
    assert mock_queue.call_count == 1
    assert mock_queue.call_args[0] == ("x", {}, False)


@pytest.mark.parametrize("elasticapm_client", [{"verify_server_cert": False}], indirect=True)
def test_client_disables_ssl_verification(elasticapm_client):
    assert not elasticapm_client.config.verify_server_cert
    assert not elasticapm_client._transport._verify_server_cert


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
    client = Client(service_name="x", transport_class=None)
    assert len(client.config.errors) == 1
    assert "TRANSPORT_CLASS" in client.config.errors

    assert client.config.disable_send


@pytest.mark.parametrize(
    "elasticapm_client",
    [
        {"collect_local_variables": "errors"},
        {"collect_local_variables": "transactions"},
        {"collect_local_variables": "all"},
        {"collect_local_variables": "something"},
    ],
    indirect=True,
)
def test_collect_local_variables_errors(elasticapm_client):
    mode = elasticapm_client.config.collect_local_variables
    try:
        1 / 0
    except ZeroDivisionError:
        elasticapm_client.capture_exception()
    event = elasticapm_client.events[ERROR][0]
    if mode in ("errors", "all"):
        assert "vars" in event["exception"]["stacktrace"][0], mode
    else:
        assert "vars" not in event["exception"]["stacktrace"][0], mode


@pytest.mark.parametrize(
    "elasticapm_client",
    [
        {"source_lines_error_library_frames": 0, "source_lines_error_app_frames": 0},
        {"source_lines_error_library_frames": 1, "source_lines_error_app_frames": 1},
        {"source_lines_error_library_frames": 7, "source_lines_error_app_frames": 3},
    ],
    indirect=True,
)
def test_collect_source_errors(elasticapm_client):
    library_frame_context = elasticapm_client.config.source_lines_error_library_frames
    in_app_frame_context = elasticapm_client.config.source_lines_error_app_frames
    try:
        import json, datetime

        json.dumps(datetime.datetime.now())
    except TypeError:
        elasticapm_client.capture_exception()
    event = elasticapm_client.events[ERROR][0]
    in_app_frame = event["exception"]["stacktrace"][0]
    library_frame = event["exception"]["stacktrace"][1]
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


def test_transaction_id_is_attached(elasticapm_client):
    elasticapm_client.capture_message("noid")
    elasticapm_client.begin_transaction("test")
    elasticapm_client.capture_message("id")
    transaction = elasticapm_client.end_transaction("test", "test")
    elasticapm_client.capture_message("noid")

    errors = elasticapm_client.events[ERROR]
    assert "transaction" not in errors[0]
    assert errors[1]["transaction_id"] == transaction.id
    assert "transaction" not in errors[2]


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

    assert transaction_obj._store.max_spans == 5
    assert transaction_obj.dropped_spans == 10
    assert len(spans) == 5
    for span in spans:
        assert span["name"] == "nodrop"
    assert transaction["span_count"] == {"dropped": 10, "started": 5}


@pytest.mark.parametrize("elasticapm_client", [{"span_frames_min_duration": 20}], indirect=True)
def test_transaction_span_frames_min_duration(elasticapm_client):
    elasticapm_client.begin_transaction("test_type")
    with elasticapm.capture_span("noframes"):
        time.sleep(0.001)
    with elasticapm.capture_span("frames"):
        time.sleep(0.040)
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
    with elasticapm.capture_span("frames"):
        time.sleep(0.040)
    elasticapm_client.end_transaction("test")

    spans = elasticapm_client.events[SPAN]

    assert len(spans) == 2
    assert spans[0]["name"] == "frames"
    assert spans[0]["stacktrace"] is not None

    assert spans[1]["name"] == "frames"
    assert spans[1]["stacktrace"] is not None


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


def test_transaction_context_is_used_in_errors(elasticapm_client):
    elasticapm_client.begin_transaction("test")
    elasticapm.tag(foo="baz")
    elasticapm.set_custom_context({"a": "b"})
    elasticapm.set_user_context(username="foo", email="foo@example.com", user_id=42)
    elasticapm_client.capture_message("x", custom={"foo": "bar"})
    transaction = elasticapm_client.end_transaction("test", "OK")
    message = elasticapm_client.events[ERROR][0]
    assert message["context"]["custom"] == {"a": "b", "foo": "bar"}
    assert message["context"]["user"] == {"username": "foo", "email": "foo@example.com", "id": 42}
    assert message["context"]["tags"] == {"foo": "baz"}
    assert "a" in transaction.context["custom"]
    assert "foo" not in transaction.context["custom"]


def test_transaction_keyword_truncation(sending_elasticapm_client):
    too_long = "x" * (KEYWORD_MAX_LENGTH + 1)
    expected = encoding.keyword_field(too_long)
    assert too_long != expected
    assert len(expected) == KEYWORD_MAX_LENGTH
    assert expected[-1] != "x"
    sending_elasticapm_client.begin_transaction(too_long)
    elasticapm.tag(val=too_long)
    elasticapm.set_user_context(username=too_long, email=too_long, user_id=too_long)
    with elasticapm.capture_span(name=too_long, span_type=too_long):
        pass
    sending_elasticapm_client.end_transaction(too_long, too_long)
    sending_elasticapm_client.close()
    assert sending_elasticapm_client.httpserver.responses[0]["code"] == 202
    span = sending_elasticapm_client.httpserver.payloads[0][1]["span"]
    transaction = sending_elasticapm_client.httpserver.payloads[0][2]["transaction"]

    assert transaction["name"] == expected
    assert transaction["type"] == expected
    assert transaction["result"] == expected

    assert transaction["context"]["user"]["id"] == expected
    assert transaction["context"]["user"]["username"] == expected
    assert transaction["context"]["user"]["email"] == expected

    assert transaction["context"]["tags"]["val"] == expected

    assert span["type"] == expected
    assert span["name"] == expected


def test_error_keyword_truncation(sending_elasticapm_client):
    too_long = "x" * (KEYWORD_MAX_LENGTH + 1)
    expected = encoding.keyword_field(too_long)

    # let's create a way too long Exception type with a way too long module name
    WayTooLongException = type(too_long.upper(), (Exception,), {})
    WayTooLongException.__module__ = too_long
    try:
        raise WayTooLongException()
    except WayTooLongException:
        sending_elasticapm_client.capture_exception(handled=False)
    error = sending_elasticapm_client.httpserver.payloads[0][1]["error"]

    assert error["exception"]["type"] == expected.upper()
    assert error["exception"]["module"] == expected


def test_message_keyword_truncation(sending_elasticapm_client):
    too_long = "x" * (KEYWORD_MAX_LENGTH + 1)
    expected = encoding.keyword_field(too_long)
    sending_elasticapm_client.capture_message(
        param_message={"message": too_long, "params": []}, logger_name=too_long, handled=False
    )

    error = sending_elasticapm_client.httpserver.payloads[0][1]["error"]

    assert error["log"]["param_message"] == expected
    assert error["log"]["message"] == too_long  # message is not truncated

    assert error["log"]["logger_name"] == expected


@pytest.mark.parametrize("sending_elasticapm_client", [{"service_name": "*"}], indirect=True)
@mock.patch("elasticapm.transport.base.Transport.queue")
def test_config_error_stops_error_send(mock_queue, sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send is True
    sending_elasticapm_client.capture_message("bla", handled=False)
    assert mock_queue.call_count == 0


@pytest.mark.parametrize("sending_elasticapm_client", [{"service_name": "*"}], indirect=True)
@mock.patch("elasticapm.transport.base.Transport.queue")
def test_config_error_stops_transaction_send(mock_queue, sending_elasticapm_client):
    assert sending_elasticapm_client.config.disable_send is True
    sending_elasticapm_client.begin_transaction("test")
    sending_elasticapm_client.end_transaction("test", "OK")
    sending_elasticapm_client.close()
    assert mock_queue.call_count == 0
