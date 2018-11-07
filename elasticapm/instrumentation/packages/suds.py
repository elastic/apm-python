from elasticapm.instrumentation.packages.base import AbstractInstrumentedModule
from elasticapm.traces import capture_span


class SUDSInstrumentation(AbstractInstrumentedModule):
    name = "suds"

    instrument_list = [("suds.client", "SoapClient.invoke")]

    def call(self, module, method, wrapped, instance, args, kwargs):
        signature = 'suds.service.' + instance.method.name
        with capture_span(signature, "suds"):
            return wrapped(*args, **kwargs)

