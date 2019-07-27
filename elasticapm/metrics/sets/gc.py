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
from __future__ import absolute_import

import gc
import timeit

from elasticapm.metrics.base_metrics import MetricsSet
from elasticapm.utils import compat


class GarbageMetrics(MetricsSet):
    START = "start"
    STOP = "stop"

    def __init__(self, registry):
        self.previous = self.get_gc_stats()
        self.gc_start = None
        if hasattr(gc, "callbacks"):  # only in Python 3.3+
            gc.callbacks.append(self.gc_callback)
        super(GarbageMetrics, self).__init__(registry)

    def collect(self):
        new = self.get_gc_stats()
        prev, self.previous = self.previous, new
        delta = {k: new[k] - prev[k] for k in new.keys()}
        for k, v in compat.iteritems(delta):
            self.counter(k).inc(v)
        return super(GarbageMetrics, self).collect()

    def get_gc_stats(self):
        stats = {}
        if hasattr(gc, "get_stats"):  # only available in Python 3.4+
            generations = gc.get_stats()
            for i, generation in enumerate(generations):
                stats["python.gc.gen{}.collections".format(i + 1)] = generation["collections"]
                stats["python.gc.gen{}.collected".format(i + 1)] = generation["collected"]
                stats["python.gc.gen{}.uncollectable".format(i + 1)] = generation["uncollectable"]
        return stats

    def gc_callback(self, phase, info):
        if phase == self.START:
            self.gc_start = timeit.default_timer()
        elif phase == self.STOP and self.gc_start:
            pause_time = timeit.default_timer() - self.gc_start
            self.counter("python.gc.pause_time.ms").inc(pause_time * 1000.0)
            self.gc_start = None
