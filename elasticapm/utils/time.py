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

"""Provides TimestampConversionUtil() class."""
import sys
import threading
import time
from collections import namedtuple


class Error(Exception):
    pass


class ClockMeasurementsFailed(Error):
    """Failure to take clocks measurements in specified iteration."""

    pass


class ClockMeasurements(namedtuple("ClockMeasurements", ["source", "destination"])):
    """Measurements of source and destination clocks.

    Source and destination are supposed to reflect nearly the same point
    in time taken from different clocks.
    """


class TimestampConversionUtil(object):
    """Converts timestamps from representation of one clock to another."""

    def __init__(self, src_timefn, dst_timefn, measurements_ttl=None):
        self.__lock = threading.Lock()
        self.__src_timefn = src_timefn
        self.__dst_timefn = dst_timefn
        self.__measurements_ttl = measurements_ttl
        self.__clocks_measurement_cache = None

    def take_clock_measurements(self, tolerance=0.00001, limit=10):
        """Attempt to take times from both clocks as close as possible."""
        if limit <= 0:
            raise ValueError("'limit' can not be less than 1. Got {}.".format(limit))
        src_now = dst_now = None
        epsilon = sys.float_info.max
        i = 0
        for _ in range(limit):
            i += 1
            src_before = self.__src_timefn()
            dst_between = self.__dst_timefn()
            src_after = self.__src_timefn()
            src_diff = src_after - src_before
            delta = abs(src_diff)
            if delta < epsilon:
                src_now = src_before + src_diff / 2
                dst_now = dst_between
                epsilon = delta
            if epsilon <= tolerance:
                break
        else:
            raise ClockMeasurementsFailed("Failed to take clock measurements in {} iteration.".format(i))
        measurements = ClockMeasurements(src_now, dst_now)
        if self.__measurements_ttl:
            with self.__lock:
                self.__clocks_measurement_cache = (time.time(), tolerance, measurements)

        return measurements

    def __check_measurements_cache(self, tolerance):
        """Check if there are cached clock measurements."""
        if self.__clocks_measurement_cache is None:
            return
        if self.__measurements_ttl is None or self.__measurements_ttl == 0:
            return
        if (self.__clocks_measurement_cache[0] + self.__measurements_ttl) > time.time():
            return
        if self.__clocks_measurement_cache[1] != tolerance:
            return
        return self.__clocks_measurement_cache[2]

    def convert_timestamp(self, timestamp, clocks_measurements=None, tolerance=0.00001, limit=10):
        """Convert timestamp between clocks representations."""
        if not clocks_measurements:
            with self.__lock:
                clocks_measurements = self.__check_measurements_cache(tolerance=tolerance)

        if not clocks_measurements:
            clocks_measurements = self.take_clock_measurements(tolerance=tolerance, limit=limit)
        return clocks_measurements.destination + (timestamp - clocks_measurements.source)


SYSTEM_TO_MONOTONIC = TimestampConversionUtil(time.time, time.monotonic, 10)
