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

import typing as t

from sanic import Sanic
from sanic.blueprint_group import BlueprintGroup
from sanic.blueprints import Blueprint
from sanic.request import Request
from sanic.response import HTTPResponse

UserInfoType = t.Tuple[t.Optional[t.Any], t.Optional[t.Any], t.Optional[t.Any]]
LabelInfoType = t.Dict[str, t.Union[str, bool, int, float]]
CustomInfoType = t.Dict[str, t.Any]

SanicRequestOrResponse = t.Union[Request, HTTPResponse]

ApmHandlerType = t.Optional[t.Callable[[Request, BaseException], t.Coroutine[t.Any, t.Any, None]]]

EnvInfoType = t.Iterable[t.Tuple[str, str]]

TransactionNameCallbackType = t.Optional[t.Callable[[Request], str]]

UserInfoCallbackType = t.Optional[t.Callable[[Request], t.Awaitable[UserInfoType]]]

CustomContextCallbackType = t.Optional[t.Callable[[SanicRequestOrResponse], t.Awaitable[CustomInfoType]]]

LabelInfoCallbackType = t.Optional[t.Callable[[SanicRequestOrResponse], t.Awaitable[LabelInfoType]]]

APMConfigType = t.Optional[t.Union[t.Dict[str, t.Any], t.Dict[bytes, t.Any]]]

ExtendableMiddlewareGroup = t.Union[Blueprint, BlueprintGroup]

AllMiddlewareGroup = t.Union[Sanic, Blueprint, BlueprintGroup]
