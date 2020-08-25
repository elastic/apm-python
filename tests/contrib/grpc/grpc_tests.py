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



from concurrent import futures
from elasticapm.contrib.grpcio import RequestHeaderValidatorInterceptor
import pytest

grpc = pytest.importorskip("grpc")  # isort:skip
from tests.contrib.grpc import helloworld_pb2
from tests.contrib.grpc.server import Greeter
from tests.contrib.grpc import helloworld_pb2
from tests.contrib.grpc import helloworld_pb2_grpc

port = 8964

class Greeter(helloworld_pb2_grpc.GreeterServicer):

    def SayHello(self, request, context):
        return helloworld_pb2.HelloReply(message="Hello, %s!" % request.name)

@pytest.fixture()
def interceptor(elasticapm_client):
    ELASTIC_APM_CONFIG = {
        "SERVICE_NAME": "grpcapp",
        "SECRET_TOKEN": "changeme",
        "RESULT_HANDLER": lambda result: "Jack" in result.message
    }
    return RequestHeaderValidatorInterceptor(
        config=ELASTIC_APM_CONFIG,
        client=elasticapm_client
    )

@pytest.fixture()
def apm_client(interceptor):
    return interceptor.client


@pytest.fixture()
def grpc_server(request, interceptor):
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=(
            interceptor,
        )
    )
    helloworld_pb2_grpc.add_GreeterServicer_to_server(Greeter(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    request.addfinalizer(lambda : server.stop(None))


def test_server(grpc_server, apm_client):
    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = helloworld_pb2_grpc.GreeterStub(channel)
        response = stub.SayHello(helloworld_pb2.HelloRequest(name="Jack"))
    assert response.message == "Hello, Jack!"
    assert len(apm_client.events) > 0
    assert apm_client.events["transaction"][0]["name"] == "gRPC /helloworld.Greeter/SayHello"
    assert apm_client.events["transaction"][0]["result"] == "SUCC"
    txid = apm_client.events["transaction"][0]['id']


def test_result_handler(grpc_server, apm_client):
    with grpc.insecure_channel(f"localhost:{port}") as channel:
        stub = helloworld_pb2_grpc.GreeterStub(channel)
        response = stub.SayHello(helloworld_pb2.HelloRequest(name="Ryan"))
    assert response.message == "Hello, Ryan!"
    assert apm_client.events["transaction"][0]["name"] == "gRPC /helloworld.Greeter/SayHello"
    assert apm_client.events["transaction"][0]["result"] == "FAIL"
