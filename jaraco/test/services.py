"""
This module provides a ServiceManager and some Service classes for a
selection of services.

The ServiceManager
acts as a collection of the services and can monitor which are running
and start services on demand. This provides an easy entry point for
managing services in a development/testing environment.
"""

from __future__ import absolute_import

import os
import sys
import logging
import subprocess
import time
import re
import datetime
import functools
import tempfile
import shutil
import random
import collections
import importlib
import urllib2
import warnings
import numbers

from jaraco.util.timing import Stopwatch
from jaraco.util import properties

from . import paths
from .socket_test import check_port, wait_for_occupied_port

__all__ = ['ServiceManager', 'Guard', 'HTTPStatus', 'MongoDBInstance']

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
        self.wait_for_occupied_port(self.port, host)

        proto = self.proto
        port = self.port
        status_path = self.status_path
        url = '%(proto)s://%(host)s:%(port)d%(status_path)s' % vars()
        while True:
            try:
                conn = urllib2.urlopen(url)
                break
            except urllib2.HTTPError:
                if timer.split() > timeout:
                    msg = ('Received status {err.code} from {self} on '
                        '{host}:{port}')
                    raise ServiceNotRunningError(msg.format(**vars()))
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

    def wait_for_occupied_port(self, port_number, host='localhost',
            timeout=1):
        if isinstance(timeout, numbers.Number):
            timeout = datetime.timedelta(seconds=timeout)
        watch = Stopwatch()
        while True:
            self.assert_running()
            try:
                return wait_for_occupied_port(host, port_number)
            except Exception:
                if watch.split() > timeout:
                    raise

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
            check_port(host, port, timeout=0.1)
        except IOError:
            return False
        return True

    @staticmethod
    def find_free_port():
        while True:
            port = random.randint(1024, 65535)
            if Service.port_free(port): break
        return port

class MongoDBFinder(paths.PathFinder):
    candidate_paths = [
        # on the path
        '',
        # 10gen Debian package
        '/usr/bin',
        # custom install in /opt
        '/opt/mongodb/bin',
        # typical Windows
        '/Program Files/MongoDB/bin',
    ]
    # allow the environment to stipulate where mongodb must
    #  be found.
    if 'MONGODB_HOME' in os.environ:
        candidate_paths = [
            os.path.join(os.environ['MONGODB_HOME'], 'bin')]
    exe = 'mongod'
    args = ['--version']

    @classmethod
    def find_binary(cls):
        return os.path.join(cls.find_root(), cls.exe)

class MongoDBService(MongoDBFinder, Subprocess, Service):
    port = 27017

    @Subprocess.PortFree()
    def start(self):
        super(MongoDBService, self).start()
        # start the daemon
        mongodb_data = os.path.join(sys.prefix, 'var', 'lib', 'mongodb')
        cmd = [
            self.find_binary(),
            '--dbpath=%(mongodb_data)s' % vars(),
        ]
        self.process = subprocess.Popen(cmd, stdout=self.get_log())
        self.wait_for_pattern('waiting for connections on port (?P<port>\d+)')
        log.info('%s listening on %s', self, self.port)

is_virtualenv = lambda: hasattr(sys, 'real_prefix')

class MongoDBInstance(MongoDBFinder, Subprocess, Service):
    @staticmethod
    def get_data_dir():
        data_dir = None
        if is_virtualenv():
            # use the virtualenv as a base to store the data
            data_dir = os.path.join(sys.prefix, 'var', 'data')
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir)
        return tempfile.mkdtemp(dir=data_dir)

    def start(self):
        super(MongoDBInstance, self).start()
        self.data_dir = self.get_data_dir()
        if not hasattr(self, 'port') or not self.port:
            self.port = self.find_free_port()
        cmd = [
            self.find_binary(),
            '--dbpath', self.data_dir,
            '--port', str(self.port),
            '--noprealloc',
        ]
        self.process = subprocess.Popen(cmd, stdout=self.get_log())
        self.wait_for_occupied_port(self.port)
        log.info('{self} listening on {self.port}'.format(**vars()))

    def get_connection(self):
        pymongo = importlib.import_module('pymongo')
        return pymongo.Connection('localhost', self.port)

    def get_connect_hosts(self):
        return ['localhost:{self.port}'.format(**vars())]

    def stop(self):
        super(MongoDBInstance, self).stop()
        shutil.rmtree(self.data_dir)


class MongoDBReplicaSet(MongoDBFinder, Service):
    replica_set_name = 'test'

    def start(self):
        super(MongoDBReplicaSet, self).start()
        self.data_root = tempfile.mkdtemp()
        self.instances = map(self.start_instance, range(3))
        # initialize the replica set
        self.instances[0].connect().admin.command(
            'replSetInitiate', self.build_config())
        # wait until the replica set is initialized
        get_repl_set_status = functools.partial(
            self.instances[0].connect().admin.command, 'replSetGetStatus', 1
        )
        errors = importlib.import_module('pymongo.errors')
        log.info('Waiting for replica set to initialize')
        while True:
            try:
                res = get_repl_set_status()
                if res.get('myState') != 1: continue
            except errors.OperationFailure:
                continue
            break

    def start_instance(self, number):
        port = self.find_free_port()
        data_dir = os.path.join(self.data_root, 'r{number}'.format(**vars()))
        os.mkdir(data_dir)
        cmd = [
            self.find_binary(),
            '--replSet', self.replica_set_name,
            '--noprealloc',
            '--smallfiles',
            '--oplogSize', '10',
            '--dbpath', data_dir,
            '--port', str(port),
        ]
        log_file = self.get_log(number)
        process = subprocess.Popen(cmd, stdout=log_file)
        wait_for_occupied_port('localhost', port)
        log.info('{self}:{number} listening on {port}'.format(**vars()))
        return InstanceInfo(data_dir, port, process, log_file)

    def get_log(self, number):
        log_name = 'r{number}.log'.format(**vars())
        log_filename = os.path.join(self.data_root, log_name)
        log_file = open(log_filename, 'a')
        return log_file

    def is_running(self):
        return hasattr(self, 'instances') and all(
            instance.process.returncode is None for instance in self.instances)

    def stop(self):
        super(MongoDBReplicaSet, self).stop()
        for instance in self.instances:
            if instance.process.returncode is None:
                instance.process.terminate()
                instance.process.wait()
            instance.log_file.close()
        del self.instances
        shutil.rmtree(self.data_root)

    def build_config(self):
        return dict(
            _id = self.replica_set_name,
            members = [
                dict(
                    _id=number,
                    host='localhost:{instance.port}'.format(**vars()),
                ) for number, instance in enumerate(self.instances)
            ]
        )

    def get_connect_hosts(self):
        return ['localhost:{instance.port}'.format(**vars())
            for instance in self.instances]

InstanceInfoBase = collections.namedtuple('InstanceInfoBase',
    'path port process log_file')
class InstanceInfo(InstanceInfoBase):
    def connect(self):
        hp = 'localhost:{self.port}'.format(**vars())
        return __import__('pymongo').Connection(hp, slave_okay=True)
