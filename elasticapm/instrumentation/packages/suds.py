from elasticapm.instrumentation.packages.base import AbstractInstrumentedModule
from elasticapm.traces import capture_span


class SUDSInstrumentation(AbstractInstrumentedModule):
    name = "suds"

    instrument_list = [("suds.client", "SoapClient.invoke"), ("suds.client", "Client.__init__")]

    def call(self, module, method, wrapped, instance, args, kwargs):
        signature = ''
        if method == 'Client.__init__':
            signature = 'Parse WSDL ' + args[0]
        elif method == 'SoapClient.invoke':
            signature = instance.method.name

        with capture_span(signature, "suds"):
            return wrapped(*args, **kwargs)
