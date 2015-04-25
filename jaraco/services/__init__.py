"""
This module provides a Service base class for
modeling management of a service, typically launched as a subprocess.

The ServiceManager (deprecated)
acts as a collection of interdependent services, can monitor which are
running, and will start services on demand. The use case for ServiceManager
has been superseded by the more elegant `pytest fixtures
<https://pytest.org/latest/fixture.html>`_ model.
"""

from __future__ import absolute_import

import os
import sys
import logging
import time
import re
import datetime
import functools
import random
import warnings

from six.moves import urllib

import portend
from jaraco.timing import Stopwatch
from jaraco.classes import properties


__all__ = ['ServiceManager', 'Guard', 'HTTPStatus', 'Subprocess', 'Dependable',
    'Service']

log = logging.getLogger(__name__)


class ServiceNotRunningError(Exception): pass


class ServiceManager(list):
    """
    A class that manages services that may be required by some of the
    unit tests. ServiceManager will start up daemon services as
    subprocesses or threads and will stop them when requested or when
    destroyed.
    """

    def __init__(self, *args, **kwargs):
        super(ServiceManager, self).__init__(*args, **kwargs)
        msg = "ServiceManager is deprecated. Use fixtures instead."
        warnings.warn(msg, DeprecationWarning)
        self.failed = set()

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.stop_all()

    @property
    def running(self):
        is_running = lambda p: p.is_running()
        return filter(is_running, self)

    def start(self, service):
        """
        Start the service, catching and logging exceptions
        """
        try:
            map(self.start_class, service.depends)
            if service.is_running(): return
            if service in self.failed:
                log.warning("%s previously failed to start", service)
                return
            service.start()
        except Exception:
            log.exception("Unable to start service %s", service)
            self.failed.add(service)

    def start_all(self):
        "Start all services registered with this manager"
        for service in self:
            self.start(service)

    def start_class(self, class_):
        """
        Start all services of a given class. If this manager doesn't already
        have a service of that class, it constructs one and starts it.
        """
        matches = filter(lambda svc: isinstance(svc, class_), self)
        if not matches:
            svc = class_()
            self.register(svc)
            matches = [svc]
        map(self.start, matches)
        return matches

    def register(self, service):
        self.append(service)

    def stop_class(self, class_):
        "Stop all services of a given class"
        matches = filter(lambda svc: isinstance(svc, class_), self)
        map(self.stop, matches)

    def stop(self, service):
        for dep_class in service.depended_by:
            self.stop_class(dep_class)
        service.stop()

    def stop_all(self):
        # even though we can stop services in order by dependency, still
        #  stop in reverse order as a reasonable heuristic.
        map(self.stop, reversed(self.running))


class Guard(object):
    "Prevent execution of a function unless arguments pass self.allowed()"
    def __call__(self, func):
        @functools.wraps(func)
        def guarded(*args, **kwargs):
            res = self.allowed(*args, **kwargs)
            if res: return func(*args, **kwargs)
        return guarded

    def allowed(self, *args, **kwargs):
        return True


class HTTPStatus(object):
    """
    Mix-in for services that have an HTTP Service for checking the status
    """

    proto = 'http'
    status_path = '/_status/system'

    def wait_for_http(self, host='localhost', timeout=15):
        timeout = datetime.timedelta(seconds=timeout)
        timer = Stopwatch()
        portend.occupied(host, self.port, timeout=1)

        proto = self.proto
        port = self.port
        status_path = self.status_path
        url = '%(proto)s://%(host)s:%(port)d%(status_path)s' % locals()
        while True:
            try:
                conn = urllib.request.urlopen(url)
                break
            except urllib.error.HTTPError:
                if timer.split() > timeout:
                    msg = ('Received status {err.code} from {self} on '
                        '{host}:{port}')
                    raise ServiceNotRunningError(msg.format(**locals()))
                time.sleep(.5)
        return conn.read()


class Subprocess(object):
    """
    Mix-in to handle common subprocess handling
    """
    def is_running(self):
        return (self.is_external()
            or hasattr(self, 'process') and self.process.returncode is None)

    def is_external(self):
        """
        A service is external if there's another process already providing
        this service, typically detected by the port already being occupied.
        """
        return getattr(self, 'external', False)

    def stop(self):
        if self.is_running() and not self.is_external():
            super(Subprocess, self).stop()
            self.process.terminate()
            self.process.wait()
            del self.process

    @properties.NonDataProperty
    def log_root(self):
        """
        Find a directory suitable for writing log files. It uses sys.prefix
        to use a path relative to the root. If sys.prefix is /usr, it's the
        system Python, so use /var/log.
        """
        var_log = os.path.join(sys.prefix, 'var', 'log').replace('/usr/var', '/var')
        if not os.path.isdir(var_log):
            os.makedirs(var_log)
        return var_log

    def get_log(self):
        log_name = self.__class__.__name__
        log_filename = os.path.join(self.log_root, log_name)
        log_file = open(log_filename, 'a')
        self.log_reader = open(log_filename, 'r')
        self.log_reader.seek(log_file.tell())
        return log_file

    def _get_more_data(self, file, timeout):
        """
        Return data from the file, if available. If no data is received
        by the timeout, then raise RuntimeError.
        """
        timeout = datetime.timedelta(seconds=timeout)
        timer = Stopwatch()
        while timer.split() < timeout:
            data = file.read()
            if data: return data
        raise RuntimeError("Timeout")

    def wait_for_pattern(self, pattern, timeout=5):
        data = ''
        pattern = re.compile(pattern)
        while True:
            self.assert_running()
            data += self._get_more_data(self.log_reader, timeout)
            res = pattern.search(data)
            if res:
                self.__dict__.update(res.groupdict())
                return

    def assert_running(self):
        process_running = self.process.returncode is None
        if not process_running:
            raise RuntimeError("Process terminated")

    class PortFree(Guard):
        def __init__(self, port=None):
            if port is not None:
                warnings.warn("Passing port to PortFree is deprecated",
                    DeprecationWarning)

        def allowed(self, service, *args, **kwargs):
            port_free = service.port_free(service.port)
            if not port_free:
                log.warning("%s already running on port %s", service,
                    service.port)
                service.external = True
            return port_free


class Dependable(type):
    """
    Metaclass to keep track of services which are depended on by others.

    When a class (cls) is created which depends on another (dep), the other gets
    a reference to cls in its depended_by attribute.
    """
    def __init__(cls, name, bases, attribs):
        type.__init__(cls, name, bases, attribs)
        # create a set in this class for dependent services to register
        cls.depended_by = set()
        for dep in cls.depends:
            dep.depended_by.add(cls)


class Service(object):
    "An abstract base class for services"
    __metaclass__ = Dependable
    depends = set()

    def start(self):
        log.info('Starting service %s', self)

    def is_running(self): return False

    def stop(self):
        log.info('Stopping service %s', self)

    def __repr__(self):
        return self.__class__.__name__ + '()'

    @staticmethod
    def port_free(port, host='localhost'):
        try:
            portend._check_port(host, port, timeout=0.1)
        except IOError:
            return False
        return True

    @staticmethod
    def find_free_port():
        while True:
            port = random.randint(1024, 65535)
            if Service.port_free(port): break
        return port
