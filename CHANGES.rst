v3.1.0
======

* Require Python 3.6 or later.

3.0
===

Moved 'envs' functionality to its own package, jaraco.envs.

2.0
===

Switch to `pkgutil namespace technique
<https://packaging.python.org/guides/packaging-namespace-packages/#pkgutil-style-namespace-packages>`_
for the ``jaraco`` namespace.

1.9
===

Refresh package metadata, including use of declarative config.

Replace deprecated ``jaraco.timing`` dependency with ``tempora``.

1.8.1
=====

#4: Use ``sys.executable`` when building tox envs.

1.8
===

Added new ``envs/ToxEnv`` which leverages tox for defining
and building environments.

1.7
===

#3: Service envs are now created in ``.cache/services`` by
default and no longer consider whether the services are
being created in a virtualenv. This allows all services to
be created in one flat system. Clients that wish to continue
to create services within a virtualenv's root should override
the envs.VirtualEnv.root property (on the class or the
instance).

1.6.1
=====

Add missing dependency on virtualenv.

1.6
===

Added ``jaraco.services.envs`` with ``VirtualEnv`` class.

1.5.2
=====

#2: Correct scope for ``port`` reference in HTTPStatus error
template.

1.5.1
=====

#1: Replace use of private ``portend._check_port`` with call to
``portend.free``.

1.5
===

In ``services.paths``, add ``PathFinder.resolve`` as a convenience
wrapper for including the resolved executable suitable for passing
to subprocess.Popen.

1.4.1
=====

Use ``path.Path`` for compatibility with path.py 10.

1.4
===

Moved project to Github.

1.3
===

In HTTPStatus.wait_for_http, allow full timeout for port to be bound.

1.2
===

Added ``PythonService`` class, which will install a Python package
into an environment and then launch a process in that
environment.

1.1
===

Add ``HTTPStatus.build_url``.
