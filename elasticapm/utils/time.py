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
import time

CLOCK_DIFF = None


def time_to_perf_counter(timestamp):
    """
    This function converts a given epoch timestamp in seconds (typically from
    `time.time()`) to the "equivalent" result from `time.perf_counter()`.

    Note that because these functions vary in their resolution and tick rate,
    this is only a close approximation.

    Note also that because `time.time()` is *usually* monotonic (but not
    guaranteed), if a system clock is changed, this function could become
    very inaccurate.
    """
    global CLOCK_DIFF
    if CLOCK_DIFF is None:
        _calculate_clock_diff()

    return timestamp + CLOCK_DIFF


def _calculate_clock_diff():
    """
    Calculate the difference between `time.perf_counter()` and `time.time()`

    Uses multiple measurements to try to minimize the tolerance in the
    measurements.

    The resulting CLOCK_DIFF can be added to any `time.time()` result to get the
    approximate equivalent `time.perf_counter()`
    """
    global CLOCK_DIFF
    best_tolerance = sys.float_info.max
    for _ in range(10):
        time1 = time.time()
        perftime = time.perf_counter()
        time2 = time.time()

        tolerance = (time2 - time1) / 2
        timetime = time1 + tolerance

        if tolerance < best_tolerance:
            best_tolerance = tolerance
            CLOCK_DIFF = perftime - timetime

        if tolerance < 0.00001:  # try to get the two time.time() calls within 20 microseconds
            break
