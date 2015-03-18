import os
import subprocess
import abc

import path
import six
from six.moves import filter


@six.add_metaclass(abc.ABCMeta)
class PathFinder(object):
    """
    A base class for locating an executable or executables.
    """
    candidate_paths = ['']
    "Potential roots to search for self.exe"

    @abc.abstractproperty
    def exe(self):
        "The target executable"

    args = []
    "Additional args to pass to the exe when testing for its suitability"

    DEV_NULL = open(os.path.devnull, 'r+')

    @classmethod
    def find_root(cls):
        try:
            result = next(cls.find_valid_roots())
        except StopIteration:
            raise RuntimeError("{cls.__name__} unable to find executables"
                .format(**vars()))
        return path.path(result)

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
