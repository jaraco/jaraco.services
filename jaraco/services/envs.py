from __future__ import unicode_literals

import os
import sys
import subprocess
import platform

import contextlib2

from path import Path


def _is_virtual():
    """
    Is this Python running in a virtualenv or a venv?
    """
    return (
        getattr(sys, 'base_prefix', sys.prefix) != sys.prefix
        or hasattr(sys, 'real_prefix')
    )


class VirtualEnv(object):
    def _get_root(self):
        default = 'services'
        if _is_virtual():
            default = os.path.join(sys.prefix, default)
        return Path(os.environ.get('SERVICES_ROOT', default))

    @property
    def dir(self):
        return self._get_root() / self.name

    def create(self):
        self.ensure_env()
        self.install()
        return self

    def ensure_env(self):
        if os.path.exists(self.dir):
            return
        cmd = [
            sys.executable,
            '-m', 'virtualenv',
            self.dir,
        ]
        with contextlib2.suppress(AttributeError):
            cmd += ['--python', self.python]
        subprocess.check_call(cmd)

    def install(self):
        cmd = [
            self.exe(),
            '-m', 'pip',
            'install',
            self.req,
        ]
        env = os.environ.copy()
        env.update(getattr(self, 'install_env', {}))
        subprocess.check_call(cmd, env=env)

    def exe(self, cmd='python'):
        bin_or_scripts = 'Scripts' if platform.system() == 'Windows' else 'bin'
        return os.path.join(self.dir, bin_or_scripts, cmd)

    def env_vars(self):
        return {}


class _VEnv(VirtualEnv):
    """
    Experimental version of VirtualEnv, requires target environment
    to be Python 3.
    """
    def ensure_env(self):
        executable = getattr(self, 'python', sys.executable)
        cmd = [
            executable,
            '-m', 'venv',
            self.dir,
        ]
        subprocess.check_call(cmd)
