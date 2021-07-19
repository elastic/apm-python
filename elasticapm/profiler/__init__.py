#  BSD 3-Clause License
#
#  Copyright (c) 2021, Elasticsearch BV
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

import time

from elasticapm.utils.logging import get_logger
from elasticapm.utils.threading import IntervalTimer, ThreadManager

try:
    from zprofile.builder import Builder
    from zprofile.cpu_profiler import CPUProfiler
except ImportError:
    CPUProfiler = None
    Builder = None

logger = get_logger("elasticapm.profiler")


if CPUProfiler and Builder:
    # zprofile doesn't set TimeNanos for some reason, so we need to fix that

    class FixedBuilder(Builder):
        def populate_profile(self, *args, **kwargs):
            super(FixedBuilder, self).populate_profile(*args, **kwargs)
            self._profile.time_nanos = time.time_ns()

    class FixedCPUProfiler(CPUProfiler):
        def _build_profile(self, duration_ns, traces):
            profile_builder = FixedBuilder()
            profile_builder.populate_profile(
                traces, self._profile_type, "nanoseconds", self._period_ms * 1000 * 1000, duration_ns
            )
            return profile_builder.emit()

    CPUProfiler = FixedCPUProfiler


class Profiler(ThreadManager):
    def __init__(self, client):
        """
        Creates a new profiler instance
        :param client: client instance
        :param tags:
        """
        self.client = client
        self._collect_timer = None
        self.transport = client._transport
        if CPUProfiler:
            self._profiler = CPUProfiler()
        super(Profiler, self).__init__()

    def collect(self):
        """
        Run a profiler session, and send the profile to APM Server
        :return:
        """
        # check again if profiler is enabled, as it can be turned on/off via remote config
        if self.client.config.profiler:
            profiler_data = self._profiler.profile(self.collect_interval)
            self.transport.send_profile(profiler_data)

    def start_thread(self, pid=None):
        super(Profiler, self).start_thread(pid=pid)
        if self.client.config.profiler_interval:
            self._collect_timer = IntervalTimer(
                self.collect, self.collect_interval, name="eapm profiler collect timer", daemon=True
            )
            logger.info("Starting profiler collect timer, interval %d", self.collect_interval)
            self._collect_timer.start()

    def stop_thread(self):
        if self._collect_timer and self._collect_timer.is_alive():
            logger.debug("Cancelling profiler collect timer")
            self._collect_timer.cancel()
            self._collect_timer = None

    @property
    def collect_interval(self):
        return self.client.config.profiler_interval / 1000.0
