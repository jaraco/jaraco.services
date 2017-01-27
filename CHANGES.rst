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
