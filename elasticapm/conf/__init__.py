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


import logging
import os
import re
import socket
import threading

from elasticapm.utils import compat, starmatch_to_regex
from elasticapm.utils.logging import get_logger

__all__ = ("setup_logging", "Config")

logger = get_logger("elasticapm.conf")


class ConfigurationError(ValueError):
    def __init__(self, msg, field_name):
        self.field_name = field_name
        super(ValueError, self).__init__(msg)


class _ConfigValue(object):
    def __init__(self, dict_key, env_key=None, type=compat.text_type, validators=None, default=None, required=False):
        self.type = type
        self.dict_key = dict_key
        self.validators = validators
        self.default = default
        self.required = required
        if env_key is None:
            env_key = "ELASTIC_APM_" + dict_key
        self.env_key = env_key

    def __get__(self, instance, owner):
        if instance:
            return instance._values.get(self.dict_key, self.default)
        else:
            return self.default

    def __set__(self, instance, value):
        value = self._validate(instance, value)
        instance._values[self.dict_key] = value

    def _validate(self, instance, value):
        if value is None and self.required:
            raise ConfigurationError(
                "Configuration error: value for {} is required.".format(self.dict_key), self.dict_key
            )
        if self.validators and value is not None:
            for validator in self.validators:
                value = validator(value, self.dict_key)
        if self.type and value is not None:
            try:
                value = self.type(value)
            except ValueError as e:
                raise ConfigurationError("{}: {}".format(self.dict_key, compat.text_type(e)), self.dict_key)
        instance._errors.pop(self.dict_key, None)
        return value


class _ListConfigValue(_ConfigValue):
    def __init__(self, dict_key, list_separator=",", **kwargs):
        self.list_separator = list_separator
        super(_ListConfigValue, self).__init__(dict_key, **kwargs)

    def __set__(self, instance, value):
        if isinstance(value, compat.string_types):
            value = value.split(self.list_separator)
        elif value is not None:
            value = list(value)
        if value:
            value = [self.type(item) for item in value]
        instance._values[self.dict_key] = value


class _DictConfigValue(_ConfigValue):
    def __init__(self, dict_key, item_separator=",", keyval_separator="=", **kwargs):
        self.item_separator = item_separator
        self.keyval_separator = keyval_separator
        super(_DictConfigValue, self).__init__(dict_key, **kwargs)

    def __set__(self, instance, value):
        if isinstance(value, compat.string_types):
            items = (item.split(self.keyval_separator) for item in value.split(self.item_separator))
            value = {key.strip(): self.type(val.strip()) for key, val in items}
        elif not isinstance(value, dict):
            # TODO: better error handling
            value = None
        instance._values[self.dict_key] = value


class _BoolConfigValue(_ConfigValue):
    def __init__(self, dict_key, true_string="true", false_string="false", **kwargs):
        self.true_string = true_string
        self.false_string = false_string
        super(_BoolConfigValue, self).__init__(dict_key, **kwargs)

    def __set__(self, instance, value):
        if isinstance(value, compat.string_types):
            if value.lower() == self.true_string:
                value = True
            elif value.lower() == self.false_string:
                value = False
        instance._values[self.dict_key] = bool(value)


class RegexValidator(object):
    def __init__(self, regex, verbose_pattern=None):
        self.regex = regex
        self.verbose_pattern = verbose_pattern or regex

    def __call__(self, value, field_name):
        value = compat.text_type(value)
        match = re.match(self.regex, value)
        if match:
            return value
        raise ConfigurationError("{} does not match pattern {}".format(value, self.verbose_pattern), field_name)


class UnitValidator(object):
    def __init__(self, regex, verbose_pattern, unit_multipliers):
        self.regex = regex
        self.verbose_pattern = verbose_pattern
        self.unit_multipliers = unit_multipliers

    def __call__(self, value, field_name):
        value = compat.text_type(value)
        match = re.match(self.regex, value, re.IGNORECASE)
        if not match:
            raise ConfigurationError("{} does not match pattern {}".format(value, self.verbose_pattern), field_name)
        val, unit = match.groups()
        try:
            val = int(val) * self.unit_multipliers[unit]
        except KeyError:
            raise ConfigurationError("{} is not a supported unit".format(unit), field_name)
        return val


duration_validator = UnitValidator(r"^((?:-)?\d+)(ms|s|m)$", r"\d+(ms|s|m)", {"ms": 1, "s": 1000, "m": 60000})
size_validator = UnitValidator(
    r"^(\d+)(b|kb|mb|gb)$", r"\d+(b|KB|MB|GB)", {"b": 1, "kb": 1024, "mb": 1024 * 1024, "gb": 1024 * 1024 * 1024}
)


class ExcludeRangeValidator(object):
    def __init__(self, range_start, range_end, range_desc):
        self.range_start = range_start
        self.range_end = range_end
        self.range_desc = range_desc

    def __call__(self, value, field_name):
        if self.range_start <= value <= self.range_end:
            raise ConfigurationError(
                "{} cannot be in range: {}".format(
                    value, self.range_desc.format(**{"range_start": self.range_start, "range_end": self.range_end})
                ),
                field_name,
            )
        return value


class FileIsReadableValidator(object):
    def __call__(self, value, field_name):
        value = os.path.normpath(value)
        if not os.path.exists(value):
            raise ConfigurationError("{} does not exist".format(value), field_name)
        elif not os.path.isfile(value):
            raise ConfigurationError("{} is not a file".format(value), field_name)
        elif not os.access(value, os.R_OK):
            raise ConfigurationError("{} is not readable".format(value), field_name)
        return value


class _ConfigBase(object):
    _NO_VALUE = object()  # sentinel object

    def __init__(self, config_dict=None, env_dict=None, inline_dict=None):
        self._values = {}
        self._errors = {}
        self.update(config_dict, env_dict, inline_dict)

    def update(self, config_dict=None, env_dict=None, inline_dict=None):
        if config_dict is None:
            config_dict = {}
        if env_dict is None:
            env_dict = os.environ
        if inline_dict is None:
            inline_dict = {}
        for field, config_value in self.__class__.__dict__.items():
            if not isinstance(config_value, _ConfigValue):
                continue
            new_value = self._NO_VALUE
            # first check environment
            if config_value.env_key and config_value.env_key in env_dict:
                new_value = env_dict[config_value.env_key]
            # check the inline config
            elif field in inline_dict:
                new_value = inline_dict[field]
            # finally, check config dictionary
            elif config_value.dict_key in config_dict:
                new_value = config_dict[config_value.dict_key]
            # only set if new_value changed. We'll fall back to the field default if not.
            if new_value is not self._NO_VALUE:
                try:
                    setattr(self, field, new_value)
                except ConfigurationError as e:
                    self._errors[e.field_name] = str(e)

    @property
    def values(self):
        return self._values

    @values.setter
    def values(self, values):
        self._values = values

    @property
    def errors(self):
        return self._errors


class Config(_ConfigBase):
    service_name = _ConfigValue("SERVICE_NAME", validators=[RegexValidator("^[a-zA-Z0-9 _-]+$")], required=True)
    environment = _ConfigValue("ENVIRONMENT", default=None)
    secret_token = _ConfigValue("SECRET_TOKEN")
    api_key = _ConfigValue("API_KEY")
    debug = _BoolConfigValue("DEBUG", default=False)
    server_url = _ConfigValue("SERVER_URL", default="http://localhost:8200", required=True)
    server_cert = _ConfigValue("SERVER_CERT", default=None, required=False, validators=[FileIsReadableValidator()])
    verify_server_cert = _BoolConfigValue("VERIFY_SERVER_CERT", default=True)
    include_paths = _ListConfigValue("INCLUDE_PATHS")
    exclude_paths = _ListConfigValue("EXCLUDE_PATHS", default=compat.get_default_library_patters())
    filter_exception_types = _ListConfigValue("FILTER_EXCEPTION_TYPES")
    server_timeout = _ConfigValue(
        "SERVER_TIMEOUT",
        type=float,
        validators=[
            UnitValidator(r"^((?:-)?\d+)(ms|s|m)?$", r"\d+(ms|s|m)", {"ms": 0.001, "s": 1, "m": 60, None: 1000})
        ],
        default=5,
    )
    hostname = _ConfigValue("HOSTNAME", default=socket.gethostname())
    auto_log_stacks = _BoolConfigValue("AUTO_LOG_STACKS", default=True)
    transport_class = _ConfigValue("TRANSPORT_CLASS", default="elasticapm.transport.http.AsyncTransport", required=True)
    processors = _ListConfigValue(
        "PROCESSORS",
        default=[
            "elasticapm.processors.sanitize_stacktrace_locals",
            "elasticapm.processors.sanitize_http_request_cookies",
            "elasticapm.processors.sanitize_http_response_cookies",
            "elasticapm.processors.sanitize_http_headers",
            "elasticapm.processors.sanitize_http_wsgi_env",
            "elasticapm.processors.sanitize_http_request_querystring",
            "elasticapm.processors.sanitize_http_request_body",
        ],
    )
    metrics_sets = _ListConfigValue(
        "METRICS_SETS",
        default=[
            "elasticapm.metrics.sets.cpu.CPUMetricSet",
            "elasticapm.metrics.sets.transactions.TransactionsMetricSet",
        ],
    )
    metrics_interval = _ConfigValue(
        "METRICS_INTERVAL",
        type=int,
        validators=[duration_validator, ExcludeRangeValidator(1, 999, "{range_start} - {range_end} ms")],
        default=30000,
    )
    breakdown_metrics = _BoolConfigValue("BREAKDOWN_METRICS", default=True)
    disable_metrics = _ListConfigValue("DISABLE_METRICS", type=starmatch_to_regex, default=[])
    central_config = _BoolConfigValue("CENTRAL_CONFIG", default=True)
    api_request_size = _ConfigValue("API_REQUEST_SIZE", type=int, validators=[size_validator], default=750 * 1024)
    api_request_time = _ConfigValue("API_REQUEST_TIME", type=int, validators=[duration_validator], default=10 * 1000)
    transaction_sample_rate = _ConfigValue("TRANSACTION_SAMPLE_RATE", type=float, default=1.0)
    transaction_max_spans = _ConfigValue("TRANSACTION_MAX_SPANS", type=int, default=500)
    stack_trace_limit = _ConfigValue("STACK_TRACE_LIMIT", type=int, default=500)
    span_frames_min_duration = _ConfigValue(
        "SPAN_FRAMES_MIN_DURATION",
        default=5,
        validators=[
            UnitValidator(r"^((?:-)?\d+)(ms|s|m)?$", r"\d+(ms|s|m)", {"ms": 1, "s": 1000, "m": 60000, None: 1})
        ],
        type=int,
    )
    collect_local_variables = _ConfigValue("COLLECT_LOCAL_VARIABLES", default="errors")
    source_lines_error_app_frames = _ConfigValue("SOURCE_LINES_ERROR_APP_FRAMES", type=int, default=5)
    source_lines_error_library_frames = _ConfigValue("SOURCE_LINES_ERROR_LIBRARY_FRAMES", type=int, default=5)
    source_lines_span_app_frames = _ConfigValue("SOURCE_LINES_SPAN_APP_FRAMES", type=int, default=0)
    source_lines_span_library_frames = _ConfigValue("SOURCE_LINES_SPAN_LIBRARY_FRAMES", type=int, default=0)
    local_var_max_length = _ConfigValue("LOCAL_VAR_MAX_LENGTH", type=int, default=200)
    local_var_list_max_length = _ConfigValue("LOCAL_VAR_LIST_MAX_LENGTH", type=int, default=10)
    local_var_dict_max_length = _ConfigValue("LOCAL_VAR_DICT_MAX_LENGTH", type=int, default=10)
    capture_body = _ConfigValue("CAPTURE_BODY", default="off")
    async_mode = _BoolConfigValue("ASYNC_MODE", default=True)
    instrument_django_middleware = _BoolConfigValue("INSTRUMENT_DJANGO_MIDDLEWARE", default=True)
    autoinsert_django_middleware = _BoolConfigValue("AUTOINSERT_DJANGO_MIDDLEWARE", default=True)
    transactions_ignore_patterns = _ListConfigValue("TRANSACTIONS_IGNORE_PATTERNS", default=[])
    service_version = _ConfigValue("SERVICE_VERSION")
    framework_name = _ConfigValue("FRAMEWORK_NAME", default=None)
    framework_version = _ConfigValue("FRAMEWORK_VERSION", default=None)
    global_labels = _DictConfigValue("GLOBAL_LABELS", default=None)
    disable_send = _BoolConfigValue("DISABLE_SEND", default=False)
    instrument = _BoolConfigValue("INSTRUMENT", default=True)
    enable_distributed_tracing = _BoolConfigValue("ENABLE_DISTRIBUTED_TRACING", default=True)
    capture_headers = _BoolConfigValue("CAPTURE_HEADERS", default=True)
    django_transaction_name_from_route = _BoolConfigValue("DJANGO_TRANSACTION_NAME_FROM_ROUTE", default=False)
    disable_log_record_factory = _BoolConfigValue("DISABLE_LOG_RECORD_FACTORY", default=False)
    use_elastic_traceparent_header = _BoolConfigValue("USE_ELASTIC_TRACEPARENT_HEADER", default=True)


class VersionedConfig(object):
    """
    A thin layer around Config that provides versioning
    """

    __slots__ = ("_config", "_version", "_first_config", "_first_version", "_lock")

    def __init__(self, config_object, version):
        """
        Create a new VersionedConfig with an initial Config object
        :param config_object: the initial Config object
        :param version: a version identifier for the configuration
        """
        self._config = self._first_config = config_object
        self._version = self._first_version = version
        self._lock = threading.Lock()

    def update(self, version, **config):
        """
        Update the configuration version
        :param version: version identifier for the new configuration
        :param config: a key/value map of new configuration
        :return: configuration errors, if any
        """
        new_config = Config()
        new_config.values = self._config.values.copy()

        # pass an empty env dict to ensure the environment doesn't get precedence
        new_config.update(inline_dict=config, env_dict={})
        if not new_config.errors:
            with self._lock:
                self._version = version
                self._config = new_config
        else:
            return new_config.errors

    def reset(self):
        """
        Reset state to the original configuration
        """
        with self._lock:
            self._version = self._first_version
            self._config = self._first_config

    @property
    def changed(self):
        return self._config != self._first_config

    def __getattr__(self, item):
        return getattr(self._config, item)

    def __setattr__(self, name, value):
        if name not in self.__slots__:
            setattr(self._config, name, value)
        else:
            super(VersionedConfig, self).__setattr__(name, value)

    @property
    def config_version(self):
        return self._version


def update_config(agent):
    logger.debug("Checking for new config...")
    transport = agent._transport
    keys = {"service": {"name": agent.config.service_name}}
    if agent.config.environment:
        keys["service"]["environment"] = agent.config.environment
    new_version, new_config, next_run = transport.get_config(agent.config.config_version, keys)
    if new_version and new_config:
        errors = agent.config.update(new_version, **new_config)
        if errors:
            logger.error("Error applying new configuration: %s", repr(errors))
        else:
            logger.info(
                "Applied new configuration: %s",
                "; ".join(
                    "%s=%s" % (compat.text_type(k), compat.text_type(v)) for k, v in compat.iteritems(new_config)
                ),
            )
    elif new_version == agent.config.config_version:
        logger.debug("Remote config unchanged")
    elif not new_config and agent.config.changed:
        logger.debug("Remote config disappeared, resetting to original")
        agent.config.reset()

    return next_run


def setup_logging(handler, exclude=("gunicorn", "south", "elasticapm.errors")):
    """
    Configures logging to pipe to Elastic APM.

    - ``exclude`` is a list of loggers that shouldn't go to ElasticAPM.

    For a typical Python install:

    >>> from elasticapm.handlers.logging import LoggingHandler
    >>> client = ElasticAPM(...)
    >>> setup_logging(LoggingHandler(client))

    Within Django:

    >>> from elasticapm.contrib.django.handlers import LoggingHandler
    >>> setup_logging(LoggingHandler())

    Returns a boolean based on if logging was configured or not.
    """
    logger = logging.getLogger()
    if handler.__class__ in map(type, logger.handlers):
        return False

    logger.addHandler(handler)

    return True
