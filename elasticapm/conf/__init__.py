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

from elasticapm.utils import compat, starmatch_to_regex

__all__ = ("setup_logging", "Config")


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
        if value is not None:
            value = self.type(value)
        instance._values[self.dict_key] = value

    def _validate(self, instance, value):
        if value is None and self.required:
            raise ConfigurationError(
                "Configuration error: value for {} is required.".format(self.dict_key), self.dict_key
            )
        if self.validators and value is not None:
            for validator in self.validators:
                value = validator(value, self.dict_key)
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
    def errors(self):
        return self._errors


class Config(_ConfigBase):
    service_name = _ConfigValue("SERVICE_NAME", validators=[RegexValidator("^[a-zA-Z0-9 _-]+$")], required=True)
    environment = _ConfigValue("ENVIRONMENT", default=None)
    secret_token = _ConfigValue("SECRET_TOKEN")
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
        default=["elasticapm.metrics.sets.cpu.CPUMetricSet", "elasticapm.metrics.sets.gc.GarbageMetrics"],
    )
    metrics_interval = _ConfigValue(
        "METRICS_INTERVAL",
        type=int,
        validators=[duration_validator, ExcludeRangeValidator(1, 999, "{range_start} - {range_end} ms")],
        default=30000,
    )
    disable_metrics = _ListConfigValue("DISABLE_METRICS", type=starmatch_to_regex, default=[])
    api_request_size = _ConfigValue("API_REQUEST_SIZE", type=int, validators=[size_validator], default=750 * 1024)
    api_request_time = _ConfigValue("API_REQUEST_TIME", type=int, validators=[duration_validator], default=10 * 1000)
    transaction_sample_rate = _ConfigValue("TRANSACTION_SAMPLE_RATE", type=float, default=1.0)
    transaction_max_spans = _ConfigValue("TRANSACTION_MAX_SPANS", type=int, default=500)
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
    capture_body = _ConfigValue("CAPTURE_BODY", default="off")
    async_mode = _BoolConfigValue("ASYNC_MODE", default=True)
    instrument_django_middleware = _BoolConfigValue("INSTRUMENT_DJANGO_MIDDLEWARE", default=True)
    transactions_ignore_patterns = _ListConfigValue("TRANSACTIONS_IGNORE_PATTERNS", default=[])
    service_version = _ConfigValue("SERVICE_VERSION")
    framework_name = _ConfigValue("FRAMEWORK_NAME", default=None)
    framework_version = _ConfigValue("FRAMEWORK_VERSION", default=None)
    disable_send = _BoolConfigValue("DISABLE_SEND", default=False)
    instrument = _BoolConfigValue("DISABLE_INSTRUMENTATION", default=True)
    enable_distributed_tracing = _BoolConfigValue("ENABLE_DISTRIBUTED_TRACING", default=True)
    capture_headers = _BoolConfigValue("CAPTURE_HEADERS", default=True)
    django_transaction_name_from_route = _BoolConfigValue("DJANGO_TRANSACTION_NAME_FROM_ROUTE", default=False)


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
