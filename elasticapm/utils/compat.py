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

import atexit
import functools
import operator
import platform
import sys
import types


def noop_decorator(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapped


def atexit_register(func):
    """
    Uses either uwsgi's atexit mechanism, or atexit from the stdlib.

    When running under uwsgi, using their atexit handler is more reliable,
    especially when using gevent
    :param func: the function to call at exit
    """
    try:
        import uwsgi

        orig = getattr(uwsgi, "atexit", None)

        def uwsgi_atexit():
            if callable(orig):
                orig()
            func()

        uwsgi.atexit = uwsgi_atexit
    except ImportError:
        atexit.register(func)


# Compatibility layer for Python2/Python3, partly inspired by /modified from six, https://github.com/benjaminp/six
# Remainder of this file: Copyright Benjamin Peterson, MIT License https://github.com/benjaminp/six/blob/master/LICENSE

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3


if PY2:
    from urllib import getproxies_environment, proxy_bypass_environment  # noqa F401

    import Queue as queue  # noqa F401
    import StringIO
    import urlparse  # noqa F401
    from urllib2 import HTTPError  # noqa F401

    StringIO = BytesIO = StringIO.StringIO

    string_types = (basestring,)  # noqa F821
    integer_types = (int, long)  # noqa F821
    class_types = (type, types.ClassType)
    text_type = unicode  # noqa F821
    binary_type = str
    irange = xrange  # noqa F821

    def b(s):
        return s

    get_function_code = operator.attrgetter("func_code")

    def iterkeys(d, **kwargs):
        return d.iterkeys(**kwargs)

    def iteritems(d, **kwargs):
        return d.iteritems(**kwargs)

    # for django.utils.datastructures.MultiValueDict
    def iterlists(d, **kw):
        return d.iterlists(**kw)

    def exec_(_code_, _globs_=None, _locs_=None):
        """Execute code in a namespace."""
        if _globs_ is None:
            frame = sys._getframe(1)
            _globs_ = frame.f_globals
            if _locs_ is None:
                _locs_ = frame.f_locals
            del frame
        elif _locs_ is None:
            _locs_ = _globs_
        exec("""exec _code_ in _globs_, _locs_""")

    exec_(
        """def reraise(tp, value, tb=None):
    try:
        raise tp, value, tb
    finally:
        tb = None
"""
    )
else:
    import io
    import queue  # noqa F401
    from urllib import parse as urlparse  # noqa F401
    from urllib.error import HTTPError  # noqa F401
    from urllib.request import getproxies_environment, proxy_bypass_environment  # noqa F401

    StringIO = io.StringIO
    BytesIO = io.BytesIO

    string_types = (str,)
    integer_types = (int,)
    class_types = (type,)
    text_type = str
    binary_type = bytes
    irange = range

    def b(s):
        return s.encode("latin-1")

    get_function_code = operator.attrgetter("__code__")

    def iterkeys(d, **kwargs):
        return iter(d.keys(**kwargs))

    def iteritems(d, **kwargs):
        return iter(d.items(**kwargs))

    # for django.utils.datastructures.MultiValueDict
    def iterlists(d, **kw):
        return iter(d.lists(**kw))

    def reraise(tp, value, tb=None):
        try:
            if value is None:
                value = tp()
            if value.__traceback__ is not tb:
                raise value.with_traceback(tb)
            raise value
        finally:
            value = None
            tb = None


def get_default_library_patters():
    """
    Returns library paths depending on the used platform.

    :return: a list of glob paths
    """
    python_version = platform.python_version_tuple()
    python_implementation = platform.python_implementation()
    system = platform.system()
    if python_implementation == "PyPy":
        if python_version[0] == "2":
            return ["*/lib-python/%s.%s/*" % python_version[:2], "*/site-packages/*"]
        else:
            return ["*/lib-python/%s/*" % python_version[0], "*/site-packages/*"]
    else:
        if system == "Windows":
            return [r"*\lib\*"]
        return ["*/lib/python%s.%s/*" % python_version[:2], "*/lib64/python%s.%s/*" % python_version[:2]]


def multidict_to_dict(d):
    """
    Turns a werkzeug.MultiDict or django.MultiValueDict into a dict with
    list values
    :param d: a MultiDict or MultiValueDict instance
    :return: a dict instance
    """
    return dict((k, v[0] if len(v) == 1 else v) for k, v in iterlists(d))


try:
    import uwsgi

    # check if a master is running before importing postfork
    if uwsgi.masterpid() != 0:
        from uwsgidecorators import postfork
    else:

        def postfork(f):
            return f


except (ImportError, AttributeError):

    def postfork(f):
        return f
