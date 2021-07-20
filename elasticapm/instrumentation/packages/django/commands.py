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
import sys

from elasticapm.instrumentation.packages.base import AbstractInstrumentedModule
from elasticapm.utils import compat


class DjangoCommandInstrumentation(AbstractInstrumentedModule):
    name = "django_command"

    instrument_list = [("django.core.management", "BaseCommand.execute")]

    def call_if_sampling(self, module, method, wrapped, instance, args, kwargs):
        from django.apps import apps  # import at top level fails if Django is not installed

        app = apps.get_app_config("elasticapm.contrib.django")
        client = getattr(app, "client", None)
        full_name = compat.text_type(instance.__module__)
        name = full_name.rsplit(".", 1)[-1]
        if not client or any(pattern.match(name) for pattern in client.config.django_commands_exclude):
            return wrapped(*args, **kwargs)

        transaction = client.begin_transaction("django_command")
        transaction.is_sampled = True  # always sample transactions
        status = "ok"
        try:
            return wrapped(*args, **kwargs)
        except Exception:
            status = "failed"
            client.capture_exception()
            compat.reraise(*sys.exc_info())
        finally:
            client.end_transaction(full_name, status)
