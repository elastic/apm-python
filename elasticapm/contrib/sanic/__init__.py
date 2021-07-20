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

import sys
import typing as t

from sanic import Sanic
from sanic.request import Request
from sanic.response import HTTPResponse

from elasticapm import label
from elasticapm import set_context as elastic_context
from elasticapm import (
    set_custom_context,
    set_transaction_name,
    set_transaction_outcome,
    set_transaction_result,
    set_user_context,
)
from elasticapm.base import Client
from elasticapm.conf import constants
from elasticapm.contrib.asyncio.traces import set_context
from elasticapm.contrib.sanic.patch import ElasticAPMPatchedErrorHandler
from elasticapm.contrib.sanic.sanic_types import (
    AllMiddlewareGroup,
    APMConfigType,
    CustomContextCallbackType,
    CustomInfoType,
    ExtendableMiddlewareGroup,
    LabelInfoCallbackType,
    TransactionNameCallbackType,
    UserInfoCallbackType,
)
from elasticapm.contrib.sanic.utils import SanicAPMConfig, get_request_info, get_response_info, make_client
from elasticapm.instrumentation.control import instrument
from elasticapm.utils.disttracing import TraceParent
from elasticapm.utils.logging import get_logger


class ElasticAPM:
    """
    Sanic App Middleware for Elastic APM Capturing

    >>> app = Sanic(name="elastic-apm-sample")

    Pass the Sanic app and let the configuration be derived from it::

    >>> apm = ElasticAPM(app=app)

    Configure the APM Client Using Custom Configurations::

    >>> apm = ElasticAPM(app=app, config={
        "SERVICE_NAME": "elastic-apm-sample",
        "SERVICE_VERSION": "v1.2.0",
        "SERVER_URL": "http://eapm-server.somdomain.com:443",
        "SECRET_TOKEN": "supersecrettokenstuff",
    })

    Pass a pre-build Clinet instance to the APM Middleware::

    >>> apm = ElasticAPM(app=app, client=Client())

    Pass arbitrary Server name and token to the client while initializing::

    >>> apm = ElasticAPM(app=app, service_name="elastic-apm-sample", secret_token="supersecretthing")

    Capture an Exception::

    >>> try:
    >>>     1 / 0
    >>> except ZeroDivisionError:
    >>>     apm.capture_exception()

    Capture generic message::

    >>> apm.capture_message("Some Nice message to be captured")
    """

    def __init__(
        self,
        app: Sanic,
        client: t.Optional[Client] = None,
        client_cls: t.Type[Client] = Client,
        config: APMConfigType = None,
        transaction_name_callback: TransactionNameCallbackType = None,
        user_context_callback: UserInfoCallbackType = None,
        custom_context_callback: CustomContextCallbackType = None,
        label_info_callback: LabelInfoCallbackType = None,
        **defaults,
    ) -> None:
        """
        Initialize an instance of the ElasticAPM client that will be used to configure the reset of the Application
        middleware

        :param app: An instance of Sanic app server
        :param client: An instance of Client if you want to leverage a custom APM client instance pre-created
        :param client_cls: Base Instance of the Elastic Client to be used to setup the APM Middleware
        :param config: Configuration values to be used for setting up the Elastic Client. This includes the APM server
        :param transaction_name_callback: Callback method used to extract the transaction name. If nothing is provided
            it will fallback to the default implementation provided by the middleware extension
        :param user_context_callback: Callback method used to extract the user context information. Will be ignored
            if one is not provided by the users while creating an instance of the ElasticAPM client
        :param custom_context_callback: Callback method used to generate custom context information for the transaction
        :param label_info_callback: Callback method used to generate custom labels/tags for the current transaction
        :param defaults: Default configuration values to be used for settings up the APM client
        """
        self._app = app  # type: Sanic
        self._client_cls = client_cls  # type: type
        self._client = client  # type: t.Union[None, Client]
        self._skip_init_middleware = defaults.pop("skip_init_middleware", False)  # type: bool
        self._skip_init_exception_handler = defaults.pop("skip_init_exception_handler", False)  # type: bool
        self._transaction_name_callback = transaction_name_callback  # type: TransactionNameCallbackType
        self._user_context_callback = user_context_callback  # type: UserInfoCallbackType
        self._custom_context_callback = custom_context_callback  # type: CustomContextCallbackType
        self._label_info_callback = label_info_callback  # type: LabelInfoCallbackType
        self._logger = get_logger("elasticapm.errors.client")
        self._client_config = {}  # type: t.Dict[str, str]
        self._setup_client_config(config=config)
        self._init_app()

    async def capture_exception(self, exc_info=None, handled=True, **kwargs):
        """
        Capture a generic exception and traceback to be reported to the APM
        :param exc_info: Exc info extracted from the traceback for the current exception
        :param handled: Boolean indicator for if the exception is handled.
        :param kwargs: additional context to be passed to the API client for capturing exception related information
        :return: None
        """
        assert self._client, "capture_exception called before application configuration is initialized"
        return self._client.capture_exception(exc_info=exc_info, handled=handled, **kwargs)

    async def capture_message(self, message=None, param_message=None, **kwargs):
        """
        Capture a generic message for the APM Client
        :param message: Message information to be captured
        :param param_message:
        :param kwargs: additional context to be passed to the API client for capturing exception related information
        :return:
        """
        assert self._client, "capture_message called before application configuration is initialized"
        return self._client.capture_message(message=message, param_message=param_message, **kwargs)

    def _setup_client_config(self, config: APMConfigType = None):
        app_based_config = SanicAPMConfig(self._app)
        if dict(app_based_config):
            self._client_config = dict(app_based_config)

        if config:
            self._client_config.update(config)

    # noinspection PyBroadException,PyUnresolvedReferences
    def _init_app(self) -> None:
        """
        Initialize all the required middleware and other application infrastructure that will perform the necessary
        capture of the APM instrumentation artifacts
        :return: None
        """
        if not self._client:
            self._client = make_client(config=self._client_config, client_cls=self._client_cls, **self._client_config)

        if not self._skip_init_exception_handler:
            self._setup_exception_manager()

        if self._client.config.instrument and self._client.config.enabled:
            instrument()
            try:
                from elasticapm.contrib.celery import register_instrumentation

                register_instrumentation(client=self._client)
            except ImportError:
                self._logger.debug(
                    "Failed to setup instrumentation. "
                    "Please install requirements for elasticapm.contrib.celery if instrumentation is required"
                )
                pass

        if not self._skip_init_middleware:
            self._setup_request_handler(entity=self._app)

    # noinspection PyMethodMayBeStatic,PyBroadException
    def _default_transaction_name_generator(self, request: Request) -> str:
        """
        Method used to extract the default transaction name. This is generated by joining the HTTP method and the
        URL path used for invoking the API handler
        :param request: Sanic HTTP Request object
        :return: string containing the Transaction name
        """
        url_template = request.path
        # Sanic's new router puts this into the request itself so that it can be accessed easily
        # On Exception with `NotFound` with new Sanic Router, the `route` object will be None
        # This check is to enforce that limitation
        if hasattr(request, "route") and request.route:
            url_template = request.route.path
            url_template = f"/{url_template}" if not url_template.startswith("/") else url_template
        else:
            # Let us fallback to using old router model to extract the info
            try:
                _, _, _, url_template, _, _ = self._app.router.get(request=request)
            except Exception:
                pass

        return f"{request.method} {url_template}"

    # noinspection PyMethodMayBeStatic
    async def _setup_default_custom_context(self, request: Request) -> CustomInfoType:
        return request.match_info

    def setup_middleware(self, entity: ExtendableMiddlewareGroup):
        """
        Adhoc registration of the middlewares for Blueprint and BlueprintGroup if you don't want to instrument
        your entire application. Only part of it can be done.
        :param entity: Blueprint or BlueprintGroup Kind of resource
        :return: None
        """
        self._setup_request_handler(entity=entity)

    def _setup_request_handler(self, entity: AllMiddlewareGroup) -> None:
        """
        This method is used to setup a series of Sanic Application level middleware so that they can be applied to all
        the routes being registered under the app easily.

        :param entity: entity: Sanic APP or Blueprint or BlueprintGroup Kind of resource
        :return: None
        """

        @entity.middleware("request")
        async def _instrument_request(request: Request):
            if not self._client.should_ignore_url(url=request.path):
                trace_parent = TraceParent.from_headers(headers=request.headers)
                self._client.begin_transaction("request", trace_parent=trace_parent)
                await set_context(
                    lambda: get_request_info(config=self._client.config, request=request),
                    "request",
                )
                self._setup_transaction_name(request=request)
                if self._user_context_callback:
                    name, email, uid = await self._user_context_callback(request)
                    set_user_context(username=name, email=email, user_id=uid)

                await self._setup_custom_context(request=request)

                if self._label_info_callback:
                    labels = await self._label_info_callback(request)
                    label(**labels)

        # noinspection PyUnusedLocal
        @entity.middleware("response")
        async def _instrument_response(request: Request, response: HTTPResponse):
            await set_context(
                lambda: get_response_info(
                    config=self._client.config,
                    response=response,
                ),
                "response",
            )
            self._setup_transaction_name(request=request)
            result = f"HTTP {response.status // 100}xx"
            set_transaction_result(result=result, override=False)
            set_transaction_outcome(http_status_code=response.status, override=False)
            elastic_context(data={"status_code": response.status}, key="response")
            self._client.end_transaction()

    def _setup_transaction_name(self, request: Request) -> None:
        """
        Method used to setup the transaction name using the provided callback or the default mode
        :param request: Incoming HTTP Request entity
        :return: None
        """
        if self._transaction_name_callback:
            name = self._transaction_name_callback(request)
        else:
            name = self._default_transaction_name_generator(request=request)
        if name:
            set_transaction_name(name, override=False)

    async def _setup_custom_context(self, request: Request):
        if self._custom_context_callback:
            set_custom_context(data=await self._custom_context_callback(request))
        else:
            set_custom_context(data=await self._setup_default_custom_context(request=request))

    # noinspection PyBroadException,PyProtectedMember
    def _setup_exception_manager(self):
        """
        Setup global exception handler where all unhandled exception can be caught and tracked to APM server
        :return:
        """

        # noinspection PyUnusedLocal
        async def _handler(request: Request, exception: BaseException):
            if not self._client:
                return

            self._client.capture_exception(
                exc_info=sys.exc_info(),
                context={
                    "request": await get_request_info(config=self._client.config, request=request),
                },
                handled=True,
            )
            self._setup_transaction_name(request=request)
            set_transaction_result(result="HTTP 5xx", override=False)
            set_transaction_outcome(outcome=constants.OUTCOME.FAILURE, override=False)
            elastic_context(data={"status_code": 500}, key="response")
            self._client.end_transaction()

        if not isinstance(self._app.error_handler, ElasticAPMPatchedErrorHandler):
            patched_client = ElasticAPMPatchedErrorHandler(current_handler=self._app.error_handler)
            patched_client.setup_apm_handler(apm_handler=_handler)
            self._app.error_handler = patched_client
        else:
            self._app.error_handler.setup_apm_handler(apm_handler=_handler)

        try:
            from elasticapm.contrib.celery import register_exception_tracking

            register_exception_tracking(client=self._client)
        except ImportError:
            self._logger.debug(
                "Failed to setup Exception tracking. "
                "Please install requirements for elasticapm.contrib.celery if exception tracking is required"
            )
            pass
