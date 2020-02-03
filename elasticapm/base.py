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


from __future__ import absolute_import

import inspect
import itertools
import logging
import os
import platform
import sys
import time
import warnings
from copy import deepcopy

import elasticapm
from elasticapm.conf import Config, VersionedConfig, constants, update_config
from elasticapm.conf.constants import ERROR
from elasticapm.metrics.base_metrics import MetricsRegistry
from elasticapm.traces import Tracer, execution_context
from elasticapm.utils import cgroup, compat, is_master_process, stacks, varmap
from elasticapm.utils.encoding import enforce_label_format, keyword_field, shorten, transform
from elasticapm.utils.logging import get_logger
from elasticapm.utils.module_import import import_string
from elasticapm.utils.threading import IntervalTimer

__all__ = ("Client",)


class Client(object):
    """
    The base ElasticAPM client, which handles communication over the
    HTTP API to the APM Server.

    Will read default configuration from the environment variable
    ``ELASTIC_APM_APP_NAME`` and ``ELASTIC_APM_SECRET_TOKEN``
    if available. ::

    >>> from elasticapm import Client

    >>> # Read configuration from environment
    >>> client = Client()

    >>> # Configure the client manually
    >>> client = Client(
    >>>     include_paths=['my.package'],
    >>>     service_name='myapp',
    >>>     secret_token='secret_token',
    >>> )

    >>> # Record an exception
    >>> try:
    >>>     1/0
    >>> except ZeroDivisionError:
    >>>     ident = client.capture_exception()
    >>>     print ("Exception caught; reference is %%s" %% ident)
    """

    logger = get_logger("elasticapm")

    def __init__(self, config=None, **inline):
        # configure loggers first
        cls = self.__class__
        self.logger = get_logger("%s.%s" % (cls.__module__, cls.__name__))
        self.error_logger = get_logger("elasticapm.errors")

        self.tracer = None
        self.processors = []
        self.filter_exception_types_dict = {}
        self._service_info = None

        config = Config(config, inline_dict=inline)
        if config.errors:
            for msg in config.errors.values():
                self.error_logger.error(msg)
            config.disable_send = True
        self.config = VersionedConfig(config, version=None)

        # Insert the log_record_factory into the logging library
        # The LogRecordFactory functionality is only available on python 3.2+
        if compat.PY3 and not self.config.disable_log_record_factory:
            record_factory = logging.getLogRecordFactory()
            # Only way to know if it's wrapped is to create a log record
            throwaway_record = record_factory(__name__, logging.DEBUG, __file__, 252, "dummy_msg", [], None)
            if not hasattr(throwaway_record, "elasticapm_labels"):
                self.logger.debug("Inserting elasticapm log_record_factory into logging")

                # Late import due to circular imports
                import elasticapm.handlers.logging as elastic_logging

                new_factory = elastic_logging.log_record_factory(record_factory)
                logging.setLogRecordFactory(new_factory)

        headers = {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
            "User-Agent": "elasticapm-python/%s" % elasticapm.VERSION,
        }

        transport_kwargs = {
            "metadata": self._build_metadata(),
            "headers": headers,
            "verify_server_cert": self.config.verify_server_cert,
            "server_cert": self.config.server_cert,
            "timeout": self.config.server_timeout,
            "max_flush_time": self.config.api_request_time / 1000.0,
            "max_buffer_size": self.config.api_request_size,
            "processors": self.load_processors(),
        }
        self._api_endpoint_url = compat.urlparse.urljoin(
            self.config.server_url if self.config.server_url.endswith("/") else self.config.server_url + "/",
            constants.EVENTS_API_PATH,
        )
        transport_class = import_string(self.config.transport_class)
        self._transport = transport_class(self._api_endpoint_url, self, **transport_kwargs)

        for exc_to_filter in self.config.filter_exception_types or []:
            exc_to_filter_type = exc_to_filter.split(".")[-1]
            exc_to_filter_module = ".".join(exc_to_filter.split(".")[:-1])
            self.filter_exception_types_dict[exc_to_filter_type] = exc_to_filter_module

        if platform.python_implementation() == "PyPy":
            # PyPy introduces a `_functools.partial.__call__` frame due to our use
            # of `partial` in AbstractInstrumentedModule
            skip_modules = ("elasticapm.", "_functools")
        else:
            skip_modules = ("elasticapm.",)

        self.tracer = Tracer(
            frames_collector_func=lambda: list(
                stacks.iter_stack_frames(
                    start_frame=inspect.currentframe(), skip_top_modules=skip_modules, config=self.config
                )
            ),
            frames_processing_func=lambda frames: self._get_stack_info_for_trace(
                frames,
                library_frame_context_lines=self.config.source_lines_span_library_frames,
                in_app_frame_context_lines=self.config.source_lines_span_app_frames,
                with_locals=self.config.collect_local_variables in ("all", "transactions"),
                locals_processor_func=lambda local_var: varmap(
                    lambda k, v: shorten(
                        v,
                        list_length=self.config.local_var_list_max_length,
                        string_length=self.config.local_var_max_length,
                        dict_length=self.config.local_var_dict_max_length,
                    ),
                    local_var,
                ),
            ),
            queue_func=self.queue,
            config=self.config,
            agent=self,
        )
        self.include_paths_re = stacks.get_path_regex(self.config.include_paths) if self.config.include_paths else None
        self.exclude_paths_re = stacks.get_path_regex(self.config.exclude_paths) if self.config.exclude_paths else None
        self._metrics = MetricsRegistry(
            self.config.metrics_interval / 1000.0, self.queue, ignore_patterns=self.config.disable_metrics
        )
        for path in self.config.metrics_sets:
            self._metrics.register(path)
        if self.config.breakdown_metrics:
            self._metrics.register("elasticapm.metrics.sets.breakdown.BreakdownMetricSet")
        compat.atexit_register(self.close)
        if self.config.central_config:
            self._config_updater = IntervalTimer(
                update_config, 1, "eapm conf updater", daemon=True, args=(self,), evaluate_function_interval=True
            )
            self._config_updater.start()
        else:
            self._config_updater = None

    def get_handler(self, name):
        return import_string(name)

    def capture(self, event_type, date=None, context=None, custom=None, stack=None, handled=True, **kwargs):
        """
        Captures and processes an event and pipes it off to Client.send.
        """
        if event_type == "Exception":
            # never gather log stack for exceptions
            stack = False
        data = self._build_msg_for_logging(
            event_type, date=date, context=context, custom=custom, stack=stack, handled=handled, **kwargs
        )

        if data:
            # queue data, and flush the queue if this is an unhandled exception
            self.queue(ERROR, data, flush=not handled)
            return data["id"]

    def capture_message(self, message=None, param_message=None, **kwargs):
        """
        Creates an event from ``message``.

        >>> client.capture_message('My event just happened!')
        """
        return self.capture("Message", message=message, param_message=param_message, **kwargs)

    def capture_exception(self, exc_info=None, handled=True, **kwargs):
        """
        Creates an event from an exception.

        >>> try:
        >>>     exc_info = sys.exc_info()
        >>>     client.capture_exception(exc_info)
        >>> finally:
        >>>     del exc_info

        If exc_info is not provided, or is set to True, then this method will
        perform the ``exc_info = sys.exc_info()`` and the requisite clean-up
        for you.
        """
        return self.capture("Exception", exc_info=exc_info, handled=handled, **kwargs)

    def queue(self, event_type, data, flush=False):
        if self.config.disable_send:
            return
        if flush and is_master_process():
            # don't flush in uWSGI master process to avoid ending up in an unpredictable threading state
            flush = False
        self._transport.queue(event_type, data, flush)

    def begin_transaction(self, transaction_type, trace_parent=None, start=None):
        """
        Register the start of a transaction on the client

        :param transaction_type: type of the transaction, e.g. "request"
        :param trace_parent: an optional TraceParent object for distributed tracing
        :param start: override the start timestamp, mostly useful for testing
        :return: the started transaction object
        """
        return self.tracer.begin_transaction(transaction_type, trace_parent=trace_parent, start=start)

    def end_transaction(self, name=None, result="", duration=None):
        """
        End the current transaction.

        :param name: optional name of the transaction
        :param result: result of the transaction, e.g. "OK" or "HTTP 2xx"
        :param duration: override duration, mostly useful for testing
        :return: the ended transaction object
        """
        transaction = self.tracer.end_transaction(result, name, duration=duration)
        return transaction

    def close(self):
        if self._metrics:
            self._metrics._stop_collect_timer()
        if self._config_updater:
            self._config_updater.cancel()
        self._transport.close()

    def get_service_info(self):
        if self._service_info:
            return self._service_info
        language_version = platform.python_version()
        if hasattr(sys, "pypy_version_info"):
            runtime_version = ".".join(map(str, sys.pypy_version_info[:3]))
        else:
            runtime_version = language_version
        result = {
            "name": keyword_field(self.config.service_name),
            "environment": keyword_field(self.config.environment),
            "version": keyword_field(self.config.service_version),
            "agent": {"name": "python", "version": elasticapm.VERSION},
            "language": {"name": "python", "version": keyword_field(platform.python_version())},
            "runtime": {
                "name": keyword_field(platform.python_implementation()),
                "version": keyword_field(runtime_version),
            },
        }
        if self.config.framework_name:
            result["framework"] = {
                "name": keyword_field(self.config.framework_name),
                "version": keyword_field(self.config.framework_version),
            }
        self._service_info = result
        return result

    def get_process_info(self):
        return {
            "pid": os.getpid(),
            "ppid": os.getppid() if hasattr(os, "getppid") else None,
            "argv": sys.argv,
            "title": None,  # Note: if we implement this, the value needs to be wrapped with keyword_field
        }

    def get_system_info(self):
        system_data = {
            "hostname": keyword_field(self.config.hostname),
            "architecture": platform.machine(),
            "platform": platform.system().lower(),
        }
        system_data.update(cgroup.get_cgroup_container_metadata())
        pod_name = os.environ.get("KUBERNETES_POD_NAME") or system_data["hostname"]
        changed = False
        if "kubernetes" in system_data:
            k8s = system_data["kubernetes"]
            k8s["pod"]["name"] = pod_name
        else:
            k8s = {"pod": {"name": pod_name}}
        # get kubernetes metadata from environment
        if "KUBERNETES_NODE_NAME" in os.environ:
            k8s["node"] = {"name": os.environ["KUBERNETES_NODE_NAME"]}
            changed = True
        if "KUBERNETES_NAMESPACE" in os.environ:
            k8s["namespace"] = os.environ["KUBERNETES_NAMESPACE"]
            changed = True
        if "KUBERNETES_POD_UID" in os.environ:
            # this takes precedence over any value from /proc/self/cgroup
            k8s["pod"]["uid"] = os.environ["KUBERNETES_POD_UID"]
            changed = True
        if changed:
            system_data["kubernetes"] = k8s
        return system_data

    def _build_metadata(self):
        data = {
            "service": self.get_service_info(),
            "process": self.get_process_info(),
            "system": self.get_system_info(),
        }
        if self.config.global_labels:
            data["labels"] = enforce_label_format(self.config.global_labels)
        return data

    def _build_msg_for_logging(
        self, event_type, date=None, context=None, custom=None, stack=None, handled=True, **kwargs
    ):
        """
        Captures, processes and serializes an event into a dict object
        """
        transaction = execution_context.get_transaction()
        span = execution_context.get_span()
        if transaction:
            transaction_context = deepcopy(transaction.context)
        else:
            transaction_context = {}
        event_data = {}
        if custom is None:
            custom = {}
        if date is not None:
            warnings.warn(
                "The date argument is no longer evaluated and will be removed in a future release", DeprecationWarning
            )
        date = time.time()
        if stack is None:
            stack = self.config.auto_log_stacks
        if context:
            transaction_context.update(context)
            context = transaction_context
        else:
            context = transaction_context
        event_data["context"] = context
        if transaction and transaction.labels:
            context["tags"] = deepcopy(transaction.labels)

        # if '.' not in event_type:
        # Assume it's a builtin
        event_type = "elasticapm.events.%s" % event_type

        handler = self.get_handler(event_type)
        result = handler.capture(self, **kwargs)
        if self._filter_exception_type(result):
            return
        # data (explicit) culprit takes over auto event detection
        culprit = result.pop("culprit", None)
        if custom.get("culprit"):
            culprit = custom.pop("culprit")

        for k, v in compat.iteritems(result):
            if k not in event_data:
                event_data[k] = v

        log = event_data.get("log", {})
        if stack and "stacktrace" not in log:
            if stack is True:
                frames = stacks.iter_stack_frames(skip=3, config=self.config)
            else:
                frames = stack
            frames = stacks.get_stack_info(
                frames,
                with_locals=self.config.collect_local_variables in ("errors", "all"),
                library_frame_context_lines=self.config.source_lines_error_library_frames,
                in_app_frame_context_lines=self.config.source_lines_error_app_frames,
                include_paths_re=self.include_paths_re,
                exclude_paths_re=self.exclude_paths_re,
                locals_processor_func=lambda local_var: varmap(
                    lambda k, v: shorten(
                        v,
                        list_length=self.config.local_var_list_max_length,
                        string_length=self.config.local_var_max_length,
                        dict_length=self.config.local_var_dict_max_length,
                    ),
                    local_var,
                ),
            )
            log["stacktrace"] = frames

        if "stacktrace" in log and not culprit:
            culprit = stacks.get_culprit(log["stacktrace"], self.config.include_paths, self.config.exclude_paths)

        if "level" in log and isinstance(log["level"], compat.integer_types):
            log["level"] = logging.getLevelName(log["level"]).lower()

        if log:
            event_data["log"] = log

        if culprit:
            event_data["culprit"] = culprit

        if "custom" in context:
            context["custom"].update(custom)
        else:
            context["custom"] = custom

        # Make sure all data is coerced
        event_data = transform(event_data)
        if "exception" in event_data:
            event_data["exception"]["handled"] = bool(handled)

        event_data["timestamp"] = int(date * 1000000)

        if transaction:
            if transaction.trace_parent:
                event_data["trace_id"] = transaction.trace_parent.trace_id
            # parent id might already be set in the handler
            event_data.setdefault("parent_id", span.id if span else transaction.id)
            event_data["transaction_id"] = transaction.id
            event_data["transaction"] = {"sampled": transaction.is_sampled, "type": transaction.transaction_type}

        return event_data

    def _filter_exception_type(self, data):
        exception = data.get("exception")
        if not exception:
            return False

        exc_type = exception.get("type")
        exc_module = exception.get("module")
        if exc_module == "None":
            exc_module = None

        if exc_type in self.filter_exception_types_dict:
            exc_to_filter_module = self.filter_exception_types_dict[exc_type]
            if not exc_to_filter_module or exc_to_filter_module == exc_module:
                if exc_module:
                    exc_name = "%s.%s" % (exc_module, exc_type)
                else:
                    exc_name = exc_type
                self.logger.info("Ignored %s exception due to exception type filter", exc_name)
                return True
        return False

    def _get_stack_info_for_trace(
        self,
        frames,
        library_frame_context_lines=None,
        in_app_frame_context_lines=None,
        with_locals=True,
        locals_processor_func=None,
    ):
        """Overrideable in derived clients to add frames/info, e.g. templates"""
        return stacks.get_stack_info(
            frames,
            library_frame_context_lines=library_frame_context_lines,
            in_app_frame_context_lines=in_app_frame_context_lines,
            with_locals=with_locals,
            include_paths_re=self.include_paths_re,
            exclude_paths_re=self.exclude_paths_re,
            locals_processor_func=locals_processor_func,
        )

    def load_processors(self):
        """
        Loads processors from self.config.processors, as well as constants.HARDCODED_PROCESSORS.
        Duplicate processors (based on the path) will be discarded.

        :return: a list of callables
        """
        processors = itertools.chain(self.config.processors, constants.HARDCODED_PROCESSORS)
        seen = {}
        # setdefault has the nice property that it returns the value that it just set on the dict
        return [seen.setdefault(path, import_string(path)) for path in processors if path not in seen]


class DummyClient(Client):
    """Sends messages into an empty void"""

    def send(self, url, **kwargs):
        return None
