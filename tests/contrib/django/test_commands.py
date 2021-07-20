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
import pytest  # isort:skip

django = pytest.importorskip("django")  # isort:skip

from django.core.management import CommandError, call_command

import pytest

from elasticapm.conf import constants
from elasticapm.utils import compat


def test_management_command(django_elasticapm_client):
    call_command("eapm_test_command")
    transaction = django_elasticapm_client.events[constants.TRANSACTION][0]
    assert transaction["type"] == "django_command"
    assert transaction["name"] == "tests.contrib.django.testapp.management.commands.eapm_test_command"
    assert transaction["result"] == "ok"

    spans = django_elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    assert spans[0]["name"] == "yay"


def test_management_command_command_error(django_elasticapm_client):
    with pytest.raises(CommandError):
        call_command("eapm_test_command", explode="yes")
    transaction = django_elasticapm_client.events[constants.TRANSACTION][0]
    assert transaction["type"] == "django_command"
    assert transaction["name"] == "tests.contrib.django.testapp.management.commands.eapm_test_command"
    assert transaction["result"] == "failed"

    exception = django_elasticapm_client.events[constants.ERROR][0]
    assert exception["culprit"] == "tests.contrib.django.testapp.management.commands.eapm_test_command.handle"
    assert exception["exception"]["message"] == "CommandError: oh no"
    assert exception["transaction_id"] == transaction["id"]


def test_management_command_other_error(django_elasticapm_client):
    with pytest.raises(ZeroDivisionError):
        call_command("eapm_test_command", explode="yes, really")
    transaction = django_elasticapm_client.events[constants.TRANSACTION][0]
    assert transaction["type"] == "django_command"
    assert transaction["name"] == "tests.contrib.django.testapp.management.commands.eapm_test_command"
    assert transaction["result"] == "failed"

    exception = django_elasticapm_client.events[constants.ERROR][0]
    assert exception["culprit"] == "tests.contrib.django.testapp.management.commands.eapm_test_command.handle"
    assert exception["exception"]["message"].startswith("ZeroDivisionError:")
    assert exception["transaction_id"] == transaction["id"]


@pytest.mark.parametrize("django_elasticapm_client", [{"django_commands_exclude": "*"}], indirect=True)
def test_management_command_ignore_all(django_elasticapm_client):
    call_command("eapm_test_command")
    assert len(django_elasticapm_client.events[constants.TRANSACTION]) == 0


@pytest.mark.parametrize(
    "django_elasticapm_client", [{"django_commands_exclude": "eapm_test_command,other_command"}], indirect=True
)
def test_management_command_ignore_exact(django_elasticapm_client):
    call_command("eapm_test_command")
    assert len(django_elasticapm_client.events[constants.TRANSACTION]) == 0


@pytest.mark.parametrize(
    "django_elasticapm_client", [{"django_commands_exclude": "eapm_test_command,other_command"}], indirect=True
)
def test_management_command_ignore_exact(django_elasticapm_client):
    call_command("eapm_test_command")
    assert len(django_elasticapm_client.events[constants.TRANSACTION]) == 0


@pytest.mark.parametrize(
    "django_elasticapm_client", [{"django_commands_exclude": "this_command,other_command"}], indirect=True
)
def test_management_command_ignore_no_match(django_elasticapm_client):
    call_command("eapm_test_command")
    assert len(django_elasticapm_client.events[constants.TRANSACTION]) == 1
