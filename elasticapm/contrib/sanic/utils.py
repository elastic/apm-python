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

from typing import Dict

from sanic import Sanic
from sanic import __version__ as version
from sanic.cookies import CookieJar
from sanic.request import Request
from sanic.response import HTTPResponse

from elasticapm.base import Client
from elasticapm.conf import Config, constants
from elasticapm.contrib.sanic.sanic_types import EnvInfoType
from elasticapm.utils import compat, get_url_dict


class SanicAPMConfig(dict):
    def __init__(self, app: Sanic):
        super(SanicAPMConfig, self).__init__()
        for _key, _v in app.config.items():
            if _key.startswith("ELASTIC_APM_"):
                self[_key.replace("ELASTIC_APM_", "")] = _v


def get_env(request: Request) -> EnvInfoType:
    """
    Extract Server Environment Information from the current Request's context
    :param request: Inbound HTTP Request
    :return: A tuple containing the attribute and it's corresponding value for the current Application ENV
    """
    for _attr in ("server_name", "server_port", "version"):
        if hasattr(request, _attr):
            yield _attr, getattr(request, _attr)


# noinspection PyBroadException
async def get_request_info(config: Config, request: Request) -> Dict[str, str]:
    """
    Generate a traceable context information from the inbound HTTP request

    :param config: Application Configuration used to tune the way the data is captured
    :param request: Inbound HTTP request
    :return: A dictionary containing the context information of the ongoing transaction
    """
    env = dict(get_env(request=request))
    env.update(dict(request.app.config))
    result = {
        "env": env,
        "method": request.method,
        "socket": {
            "remote_address": _get_client_ip(request=request),
            "encrypted": request.scheme in ["https", "wss"],
        },
        "cookies": request.cookies,
        "http_version": request.version,
    }
    if config.capture_headers:
        result["headers"] = dict(request.headers)

    if request.method in constants.HTTP_WITH_BODY and config.capture_body:
        if request.content_type.startswith("multipart") or "octet-stream" in request.content_type:
            result["body"] = "[DISCARDED]"
        try:
            result["body"] = request.body.decode("utf-8")
        except Exception:
            pass

    if "body" not in result:
        result["body"] = "[REDACTED]"
    result["url"] = get_url_dict(request.url)
    return result


async def get_response_info(config: Config, response: HTTPResponse) -> Dict[str, str]:
    """
    Generate a traceable context information from the inbound HTTP Response

    :param config: Application Configuration used to tune the way the data is captured
    :param response: outbound HTTP Response
    :return: A dictionary containing the context information of the ongoing transaction
    """
    result = {
        "cookies": _transform_response_cookie(cookies=response.cookies),
        "finished": True,
        "headers_sent": True,
    }
    if isinstance(response.status, compat.integer_types):
        result["status_code"] = response.status

    if config.capture_headers:
        result["headers"] = dict(response.headers)

    if config.capture_body and "octet-stream" not in response.content_type:
        result["body"] = response.body.decode("utf-8")
    else:
        result["body"] = "[REDACTED]"

    return result


def _get_client_ip(request: Request) -> str:
    """Extract Client IP Address Information"""
    try:
        return request.ip or request.socket[0] or request.remote_addr
    except IndexError:
        return request.remote_addr


def make_client(client_cls=Client, **defaults) -> Client:
    if "framework_name" not in defaults:
        defaults["framework_name"] = "sanic"
        defaults["framework_version"] = version

    return client_cls(**defaults)


def _transform_response_cookie(cookies: CookieJar) -> Dict[str, str]:
    """Transform the Sanic's CookieJar instance into a Normal dictionary to build the context"""
    return {k: {"value": v.value, "path": v["path"]} for k, v in cookies.items()}
