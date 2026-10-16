import ctypes as C
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from prism.reflex_bridge import ReflexBridge
from trainer import build_decision_table

ROOT = Path(__file__).resolve().parent


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'table.bin'

    def engine(self, rows, **options):
        build_decision_table(rows, bits=4, output=self.path)
        result = ReflexBridge(ROOT, table_path=self.path, **options)
        self.addCleanup(result.close)
        self.assertTrue(result.ready, result.error)
        return result

    def test_mmap_hit_unicode_unknown_and_close(self):
        engine = self.engine([('known', 1), ('caf\u00e9', 2)])
        self.assertIsNotNone(engine._mapping if os.name == 'nt' else engine._shared)
        self.assertEqual(engine.decide('known'), 1)
        self.assertEqual(engine.decide('caf\u00e9'), 2)
        self.assertEqual(engine.decide('unknown'), -1)
        engine.close()
        self.assertEqual(engine.decide('known'), -1)

    def test_low_confidence_and_zero_label(self):
        engine = self.engine([('hold', 0), ('mixed', 1), ('mixed', 2)])
        self.assertEqual(engine.decide('hold'), 0)
        self.assertEqual(engine.decide('mixed'), -1)

    def test_invalid_files_and_missing_library_fall_back(self):
        self.path.write_bytes(b'invalid')
        with ReflexBridge(ROOT, table_path=self.path) as engine:
            self.assertFalse(engine.ready)
            self.assertEqual(engine.decide('any'), -1)
        build_decision_table([('known', 1)], bits=4, output=self.path)
        with ReflexBridge(self.tmp.name, table_path=self.path) as engine:
            self.assertFalse(engine.ready)
            self.assertEqual(engine.decide('known'), -1)

    def test_payload_limits_and_invalid_unicode(self):
        engine = self.engine([('known', 1)])
        for text in ['x' * 4097, '\u20ac' * 1500, '\ud800', None]:
            self.assertEqual(engine.decide(text), -1)

    def test_threaded_decisions_and_second_owner_rejected(self):
        engine = self.engine([('known', 2)])
        with ReflexBridge(ROOT, table_path=self.path) as duplicate:
            self.assertFalse(duplicate.ready)
            self.assertEqual(duplicate.decide('known'), -1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(engine.decide, ['known'] * 500)), [2] * 500)

    def test_cpu_affinity_restores(self):
        if os.name == 'nt':
            kernel = C.WinDLL('kernel32')
            kernel.GetCurrentProcess.restype = C.c_void_p
            kernel.GetProcessAffinityMask.argtypes = [C.c_void_p, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)]
            def mask():
                allowed, system = C.c_size_t(), C.c_size_t()
                self.assertTrue(kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(), C.byref(allowed), C.byref(system)))
                return allowed.value
            original = mask()
            engine = self.engine([('known', 1)])
            self.assertEqual(mask(), 1 << engine.cpu)
            engine.close()
            self.assertEqual(mask(), original)
        else:
            original = os.sched_getaffinity(0)
            engine = self.engine([('known', 1)])
            self.assertEqual(os.sched_getaffinity(0), {engine.cpu})
            engine.close()
            self.assertEqual(os.sched_getaffinity(0), original)

    def test_public_function_and_fallback_dispatch(self):
        build_decision_table([('known', 1)], bits=4, output=self.path)
        program = "from prism.reflex_bridge import initialize_reflex, reflex_decide, shutdown_reflex; initialize_reflex(table_path=" + repr(str(self.path)) + "); assert reflex_decide('known') == 1; calls=[]; result=reflex_decide('new'); calls.append('llm') if result == -1 else calls.append('label'); assert calls == ['llm']; shutdown_reflex()"
        result = subprocess.run([sys.executable, '-c', program], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
