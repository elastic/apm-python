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
import itertools
import os

import pytest

from pytest_bdd import given, parsers, scenario, scenarios, then, when

pytestmark = pytest.mark.bdd

version_counter = itertools.count(0)

scenarios(os.path.join("features", "api_key.feature"))


@given("an agent")
def an_agent(elasticapm_client):
    return elasticapm_client


@when(parsers.parse("an api key is set to '{key}' in the config"))
def set_api_key_to_value(an_agent, key):
    an_agent.config.update(version=next(version_counter), api_key=key)


@when("an api key is set in the config")
def set_any_api_key(an_agent):
    an_agent.config.update(next(version_counter), api_key="foo")


@when("a secret_token is set in the config")
def set_any_secret_token(an_agent):
    an_agent.config.update(next(version_counter), secret_token="foo")


@when("an api key is not set in the config")
def unset_api_key(an_agent):
    an_agent.config.update(next(version_counter), api_key=None)


@then(parsers.parse("the Authorization header is '{full_key}'"))
def authorization_full_key(an_agent, key, full_key):
    auth_headers = an_agent._transport.auth_headers
    assert "Authorization" in auth_headers
    assert auth_headers["Authorization"] == full_key


@then("the api key is sent in the Authorization header")
def authorization_api_key(an_agent):
    auth_headers = an_agent._transport.auth_headers
    assert "Authorization" in auth_headers
    assert auth_headers["Authorization"].startswith("ApiKey ")


@then("the secret token is sent in the Authorization header")
def authorization_secret_token(an_agent):
    auth_headers = an_agent._transport.auth_headers
    assert "Authorization" in auth_headers
    assert auth_headers["Authorization"].startswith("Bearer ")
