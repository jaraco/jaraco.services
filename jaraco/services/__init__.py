"""
This module provides a Service base class for
modeling management of a service, typically launched as a subprocess.

The ServiceManager (deprecated)
acts as a collection of interdependent services, can monitor which are
running, and will start services on demand. The use case for ServiceManager
has been superseded by the more elegant `pytest fixtures
<https://pytest.org/latest/fixture.html>`_ model.
"""

import os
import sys
import logging
import time
import re
import datetime
import functools
import warnings
import subprocess
import urllib.request
from typing import Set

import path
import portend
from tempora.timing import Stopwatch
from jaraco.classes import properties


__all__ = [
    'ServiceManager',
    'Guard',
    'HTTPStatus',
    'Subprocess',
    'Dependable',
    'Service',
]


log = logging.getLogger(__name__)


class ServiceNotRunningError(Exception):
    pass


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
        def is_running(p):
            return p.is_running()

        return filter(is_running, self)

    def start(self, service):
        """
        Start the service, catching and logging exceptions
        """
        try:
            map(self.start_class, service.depends)
            if service.is_running():
                return
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


class Guard:
    """
    Prevent execution of a function unless arguments pass self.allowed()

    >>> class OnlyInts(Guard):
    ...     def allowed(self, *args, **kwargs):
    ...         return all(isinstance(arg, int) for arg in args)
    >>> @OnlyInts()
    ... def the_func(val):
    ...     print(val)
    >>> the_func(1)
    1
    >>> the_func('1')
    >>> the_func(1, '1') is None
    True
    """

    def __call__(self, func):
        @functools.wraps(func)
        def guarded(*args, **kwargs):
            res = self.allowed(*args, **kwargs)
            if res:
                return func(*args, **kwargs)

        return guarded

    def allowed(self, *args, **kwargs):
        return True


class HTTPStatus:
    """
    Mix-in for services that have an HTTP Service for checking the status
    """

    proto = 'http'
    status_path = '/_status/system'
    __url = '{self.proto}://{host}:{self.port}{path}'
    __err = 'Received status {err.code} from {self} on {host}:{self.port}'

    def build_url(self, path, host='localhost'):
        return self.__url.format(**locals())

    def wait_for_http(self, host='localhost', timeout=15):
        timeout = datetime.timedelta(seconds=timeout)
        timer = Stopwatch()
        portend.occupied(host, self.port, timeout=timeout)

        url = self.build_url(self.status_path)
        while True:
            try:
                conn = urllib.request.urlopen(url)
                break
            # comment below workaround for PyCQA/pyflakes#376
            except urllib.error.HTTPError as err:  # noqa: F841
                if timer.split() > timeout:
                    raise ServiceNotRunningError(self.__err.format(**locals()))
                time.sleep(0.5)
        return conn.read()


class Subprocess:
    """
    Mix-in to handle common subprocess handling
    """

    def is_running(self):
        return (
            self.is_external()
            or hasattr(self, 'process')
            and self.process.returncode is None
        )

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
            if data:
                return data
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
                warnings.warn(
                    "Passing port to PortFree is deprecated", DeprecationWarning
                )

        def allowed(self, service, *args, **kwargs):
            port_free = service.port_free(service.port)
            if not port_free:
                log.warning("%s already running on port %s", service, service.port)
                service.external = True
            return port_free


class Dependable(type):
    """
    Metaclass to keep track of services which are depended on
    by others.

    When a class (cls) is created which depends on another
    (dep), the other gets a reference to cls in its depended_by
    attribute.
    """

    def __init__(cls, name, bases, attribs):
        type.__init__(cls, name, bases, attribs)
        # create a set in this class for dependent services to register
        cls.depended_by = set()
        for dep in cls.depends:
            dep.depended_by.add(cls)


class Service:
    "An abstract base class for services"
    __metaclass__ = Dependable
    depends: Set[str] = set()

    def start(self):
        log.info('Starting service %s', self)

    def is_running(self):
        return False

    def stop(self):
        log.info('Stopping service %s', self)

    def __repr__(self):
        return self.__class__.__name__ + '()'

    @staticmethod
    def port_free(port, host='localhost'):
        try:
            portend.free(host, port, timeout=0.1)
        except portend.Timeout:
            return False
        return True

    @staticmethod
    def find_free_port():
        msg = "Use portend.find_available_local_port"
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        return portend.find_available_local_port()


class PythonService(Service, Subprocess):
    """
    A service created by installing a package_spec into an environment and
    invoking a command.
    """

    installer = 'pip install'
    python = os.path.basename(sys.executable)

    @property
    def name(self):
        return self.__class__.__name__.lower()

    @property
    def package_spec(self):
        return self.name

    @property
    def command(self):
        return [self.python, '-m', self.name]

    @property
    def _run_env(self):
        """
        Augment the current environment providing the PYTHONUSERBASE.
        """
        env = dict(os.environ)
        env.update(getattr(self, 'env', {}), PYTHONUSERBASE=self.env_path, PIP_USER="1")
        self._disable_venv(env)
        return env

    def _disable_venv(self, env):
        """
        Disable virtualenv and venv in the environment.
        """
        venv = env.pop('VIRTUAL_ENV', None)
        if venv:
            venv_path, sep, env['PATH'] = env['PATH'].partition(os.pathsep)

    def create_env(self):
        """
        Create a PEP-370 environment
        """
        root = path.Path(os.environ.get('SERVICES_ROOT', 'services'))
        self.env_path = (root / self.name).abspath()
        cmd = [self.python, '-c', 'import site; print(site.getusersitepackages())']
        out = subprocess.check_output(cmd, env=self._run_env)
        site_packages = out.decode().strip()
        path.Path(site_packages).makedirs_p()

    def install(self):
        installer = self.installer.split()
        cmd = [self.python, '-m'] + installer + [self.package_spec]
        subprocess.check_call(cmd, env=self._run_env)

    def start(self):
        super(PythonService, self).start()
        self.create_env()
        self.install()
        output = (self.env_path / 'output.txt').open('ab')
        self.process = subprocess.Popen(
            self.command, env=self._run_env, stdout=output, stderr=subprocess.STDOUT
        )
