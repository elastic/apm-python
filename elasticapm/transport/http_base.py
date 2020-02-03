# -*- coding: utf-8 -*-

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

from elasticapm.conf import constants
from elasticapm.transport.base import AsyncTransport, Transport
from elasticapm.utils import compat


class HTTPTransportBase(Transport):
    def __init__(
        self,
        url,
        client,
        verify_server_cert=True,
        compress_level=5,
        metadata=None,
        headers=None,
        timeout=None,
        server_cert=None,
        **kwargs
    ):
        self._url = url
        self._verify_server_cert = verify_server_cert
        self._server_cert = server_cert
        self._timeout = timeout
        self._headers = {
            k.encode("ascii")
            if isinstance(k, compat.text_type)
            else k: v.encode("ascii")
            if isinstance(v, compat.text_type)
            else v
            for k, v in (headers if headers is not None else {}).items()
        }
        base, sep, tail = self._url.rpartition(constants.EVENTS_API_PATH)
        self._config_url = "".join((base, constants.AGENT_CONFIG_PATH, tail))
        super(HTTPTransportBase, self).__init__(client, metadata=metadata, compress_level=compress_level, **kwargs)

    def send(self, data):
        """
        Sends a request to a remote APM Server using HTTP POST.

        Returns the shortcut URL of the recorded error on Elastic APM
        """
        raise NotImplementedError()

    def get_config(self, current_version=None, keys=None):
        """
        Gets configuration from a remote APM Server

        :param current_version: version of the current configuration
        :param keys: a JSON-serializable dict to identify this instance, e.g.
                {
                    "service": {
                        "name": "foo",
                        "environment": "bar"
                    }
                }
        :return: a three-tuple of new version, config dictionary and validity in seconds.
                 Any element of the tuple can be None.
        """
        raise NotImplementedError()

    @property
    def auth_headers(self):
        if self.client.config.api_key:
            return {"Authorization": "ApiKey " + self.client.config.api_key}
        elif self.client.config.secret_token:
            return {"Authorization": "Bearer " + self.client.config.secret_token}
        return {}


class AsyncHTTPTransportBase(AsyncTransport, HTTPTransportBase):
    async_mode = True
    sync_transport = HTTPTransportBase
