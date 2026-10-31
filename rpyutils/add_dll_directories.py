# Copyright 2020 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from threading import RLock
from types import TracebackType
from typing import Dict, Generator, List, Optional, Protocol, Type


class _NativeDirectory(Protocol):

    def close(self) -> None:
        ...


@dataclass
class _DirectoryRecord:
    handle: _NativeDirectory
    references: int = 0


_directory_lock = RLock()
_active_directories: Dict[str, _DirectoryRecord] = {}


class _DirectoryLease:
    """One caller's ownership of a shared Windows directory registration."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._key = os.path.normcase(path)
        self._closed = True

    def close(self) -> None:
        with _directory_lock:
            if self._closed:
                return
            record = _active_directories[self._key]
            if record.references == 1:
                record.handle.close()
                del _active_directories[self._key]
            else:
                record.references -= 1
            self._closed = True

    def __enter__(self) -> _DirectoryLease:
        return self

    def __exit__(
        self, exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        self.close()

    @classmethod
    def acquire(cls, path: str) -> _DirectoryLease:
        lease = cls(path)
        with _directory_lock:
            record = _active_directories.get(lease._key)
            if record is None:
                if sys.platform != 'win32' or not hasattr(os, 'add_dll_directory'):
                    raise OSError('Windows DLL directory registration is unavailable')
                handle = os.add_dll_directory(path)
                try:
                    record = _DirectoryRecord(handle)
                    _active_directories[lease._key] = record
                except BaseException:
                    handle.close()
                    raise
            record.references += 1
            lease._closed = False
        return lease


@contextmanager
def add_dll_directories_from_env(env_name: str) -> Generator[List[_DirectoryLease], None, None]:
    """
    Add a list of directories from an environment variable to the DLL search path on Windows.

    Each directory in the environment variable that exists is passed to
    :func:`os.add_dll_directory`.

    If this function is called on a system other than Windows, then nothing happens and
    an empty list is returned.
    If this function is called with a version of Python less than 3.8, then nothing happens and
    an empty list is returned.

    Example usage::

        with add_dll_directories_from_env('PATH'):
            importlib.import_module('foo', package='bar')

    :param env_name: The name of the environment variable with DLL search paths.
    :return: A list of handles to directories.
    """
    dll_dir_handles: List[_DirectoryLease] = []
    # Only registration/refcounts hold the lock. Python imports and module
    # initialization run outside it, including recursively importing modules.
    # ExitStack also unwinds registrations if preparing a later path fails.
    with ExitStack() as cleanup:
        if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
            env_value = os.environ.get(env_name)
            path_list = env_value.split(os.pathsep) if env_value is not None else []
            for prefix_path in path_list:
                if os.path.isdir(prefix_path):
                    lease = _DirectoryLease.acquire(os.path.abspath(prefix_path))
                    try:
                        cleanup.callback(lease.close)
                    except BaseException:
                        lease.close()
                        raise
                    dll_dir_handles.append(lease)
        yield dll_dir_handles
