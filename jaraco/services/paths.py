import os
import subprocess
import abc

import path
import six
from six.moves import filter


class PathFinder(object):
    """
    A base class for locating an executable or executables.
    """
    candidate_paths = ['']
    "Potential roots to search for self.exe"

    def exe(self):
        "The target executable"
    if not six.PY2:
        exe = abc.abstractproperty(exe)

    args = []
    "Additional args to pass to the exe when testing for its suitability"

    DEV_NULL = open(os.path.devnull, 'r+')

    @classmethod
    def resolve(cls):
        """
        Resolve an executable or raise RuntimeError if one
        cannot be found.
        """
        return cls.find_root() / cls.exe

    @classmethod
    def find_root(cls):
        try:
            result = next(cls.find_valid_roots())
        except StopIteration:
            raise RuntimeError("{cls.__name__} unable to find executables"
                .format(**vars()))
        return path.Path(result)

    @classmethod
    def find_valid_roots(cls):
        """
        Generate valid roots for the target executable based on the
        candidate paths.
        """
        return filter(cls.is_valid_root, cls.candidate_paths)

    @classmethod
    def is_valid_root(cls, root):
        try:
            cmd = [os.path.join(root, cls.exe)] + cls.args
            subprocess.check_call(cmd, stdout=cls.DEV_NULL)
        except OSError:
            return False
        return True

if not six.PY2:
    PathFinder = six.add_metaclass(abc.ABCMeta)(PathFinder)
