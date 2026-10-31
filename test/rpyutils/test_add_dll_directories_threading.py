# Copyright 2026 StableKite contributors
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

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from rpyutils import add_dll_directories_from_env


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DLL registration API')
def test_concurrent_directory_leases_share_real_registration(monkeypatch, tmp_path):
    native_add = os.add_dll_directory
    registrations = []
    native_closes = []

    class ObservedHandle:
        def __init__(self, path):
            self.native = native_add(path)
            registrations.append(path)

        def close(self):
            self.native.close()
            native_closes.append(True)

    monkeypatch.setattr(os, 'add_dll_directory', ObservedHandle)
    # Keep every original PATH entry's lease while sharing the native cookie.
    monkeypatch.setenv('RPYUTILS_OWNED_TEST_DIRS', os.pathsep.join([str(tmp_path)] * 96))
    opened = Barrier(9, timeout=10)
    finished = Barrier(9, timeout=10)

    def worker(index):
        with add_dll_directories_from_env('RPYUTILS_OWNED_TEST_DIRS') as leases:
            assert len(leases) == 96
            opened.wait()
            finished.wait()
        return index

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, index) for index in range(8)]
        opened.wait()
        assert len(registrations) == 1 and native_closes == []
        finished.wait()
        assert [f.result(timeout=20) for f in futures] == list(range(8))
    assert len(native_closes) == 1


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DLL registration API')
def test_failed_path_preparation_closes_prior_real_cookie(monkeypatch, tmp_path):
    native_add = os.add_dll_directory
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    closed = []

    class ObservedHandle:
        def __init__(self, path):
            self.native = native_add(path)

        def close(self):
            self.native.close()
            closed.append(True)

    def fail_second(path):
        if path == str(second):
            raise OSError('owned registration failure')
        return ObservedHandle(path)

    monkeypatch.setattr(os, 'add_dll_directory', fail_second)
    monkeypatch.setenv('RPYUTILS_OWNED_TEST_DIRS', os.pathsep.join([str(first), str(second)]))
    with pytest.raises(OSError, match='owned registration failure'):
        with add_dll_directories_from_env('RPYUTILS_OWNED_TEST_DIRS'):
            pytest.fail('preparation failure must propagate before yielding')
    assert closed == [True]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DLL registration API')
def test_closing_one_lease_does_not_close_another(monkeypatch, tmp_path):
    native_add = os.add_dll_directory
    closed = []

    class ObservedHandle:
        def __init__(self, path):
            self.native = native_add(path)

        def close(self):
            self.native.close()
            closed.append(True)

    monkeypatch.setattr(os, 'add_dll_directory', ObservedHandle)
    monkeypatch.setenv('RPYUTILS_OWNED_TEST_DIRS', str(tmp_path))
    with add_dll_directories_from_env('RPYUTILS_OWNED_TEST_DIRS') as outer:
        with add_dll_directories_from_env('RPYUTILS_OWNED_TEST_DIRS') as inner:
            outer[0].close()
            outer[0].close()
            assert closed == []
            with inner[0]:
                assert inner[0].path == str(tmp_path)
            assert closed == [True]
    assert closed == [True]
