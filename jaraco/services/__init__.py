"""
This module provides a Service base class for
modeling management of a service, typically launched as a subprocess.
"""

import os
import sys
import logging
import time
import re
import datetime
import functools
import subprocess
import urllib.request
import warnings

import path
import portend
from tempora.timing import Stopwatch
from jaraco.classes import properties


__all__ = [
    'Guard',
    'HTTPStatus',
    'Subprocess',
    'Service',
]


log = logging.getLogger(__name__)


class ServiceNotRunningError(Exception):
    pass


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
            warnings.warn(
                "Guard is deprecated. File an issue with jaraco.services if you encounter this message.",
                DeprecationWarning,
                stacklevel=3,
            )
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

    def build_url(self, path, host='localhost'):
        return f'{self.proto}://{host}:{self.port}{path}'

    def wait_for_http(self, host='localhost', timeout=15):
        timeout = datetime.timedelta(seconds=timeout)
        timer = Stopwatch()
        portend.occupied(host, self.port, timeout=timeout)

        url = self.build_url(self.status_path)
        while True:
            try:
                conn = urllib.request.urlopen(url)
                break
            except urllib.error.HTTPError as err:
                if timer.split() > timeout:
                    raise ServiceNotRunningError(
                        f'Received status {err.code} from {self} on {host}:{self.port}'
                    )
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
            super().stop()
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
        self.log_reader = open(log_filename)
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
        def allowed(self, service, *args, **kwargs):
            port_free = service.port_free(service.port)
            if not port_free:
                log.warning("%s already running on port %s", service, service.port)
                service.external = True
            return port_free


class Service:
    "An abstract base class for services"

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
        warnings.warn(
            "PythonService is deprecated. "
            "If anyone is still using it, please file an issue with jaraco.services."
        )
        super().start()
        self.create_env()
        self.install()
        output = (self.env_path / 'output.txt').open('ab')
        self.process = subprocess.Popen(
            self.command, env=self._run_env, stdout=output, stderr=subprocess.STDOUT
        )
