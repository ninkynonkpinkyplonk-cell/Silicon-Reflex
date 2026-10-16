import ctypes as C
import multiprocessing as mp
from pathlib import Path
import tempfile
import unittest
import uuid
from shared_memory import DecisionTable, FastChannel, load_core
from trainer import build_decision_table


def consumer(name, table_path, ready):
    core = load_core()
    table = DecisionTable(core, table_path)
    with FastChannel(name, core=core, create=False) as channel:
        ready.set()
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if channel.process():
                break
            time.sleep(0)
        else:
            raise TimeoutError('No request received')
    table.close()


class ReflexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.tmp.name) / 'table.bin'
        self.core = load_core()
        self.name = 'Local\\ReflexTest-' + uuid.uuid4().hex

    def tearDown(self):
        self.core.core_unbind()
        self.tmp.cleanup()

    def train(self, rows, bits=10):
        report = build_decision_table(rows, bits=bits, output=self.path)
        self.table = DecisionTable(self.core, self.path)
        self.addCleanup(self.table.close)
        return report

    def test_xxhash_vectors(self):
        for data, expected in [(b'', 0xef46db3751d8e999), (b'a', 0xd24ec4f1a98c6e5b),
                               (b'abc', 0x44bc2cf5ad770999)]:
            self.assertEqual(self.core.core_hash(data, len(data)), expected)

    def test_bytes_hash_threshold_and_unknown(self):
        self.train([('buy', 1), ('buy', 1), ('buy', 2)])
        with FastChannel(self.name, core=self.core) as ch:
            self.assertIsNone(ch.read_decision())
            ch.write_input(b'buy')
            with self.assertRaises(RuntimeError):
                ch.write_input(b'other')
            ch.process()
            self.assertEqual(ch.read_decision(), 1)
            ch.threshold = 200
            ch.write_hash(self.core.core_hash(b'buy', 3))
            ch.process()
            self.assertEqual(ch.read_decision(), 255)
            ch.write_input(b'unknown')
            ch.process()
            self.assertEqual(ch.read_decision(), 255)
            with self.assertRaises(ValueError):
                ch.write_input(b'x' * 4097)

    def test_collision_prefers_confidence_and_abstains_for_loser(self):
        buckets = {}
        for i in range(100):
            text = str(i)
            key = self.core.core_hash(text.encode(), len(text))
            slot = key & 1
            if slot in buckets:
                loser, winner = buckets[slot], text
                break
            buckets[slot] = text
        report = self.train([(loser, 0), (loser, 1), (winner, 2)], bits=1)
        self.assertEqual(report['collisions'], 1)
        for text, expected in [(loser, 255), (winner, 2)]:
            key = self.core.core_hash(text.encode(), len(text))
            self.assertEqual(self.core.core_lookup(key, 0), expected)

    def test_two_process_named_mapping(self):
        self.train([('shared', 2)])
        with FastChannel(self.name, core=self.core) as channel:
            ready = mp.Event()
            process = mp.Process(target=consumer, args=(self.name, str(self.path), ready))
            process.start()
            try:
                self.assertTrue(ready.wait(10))
                channel.write_input(b'shared')
                self.assertEqual(channel.wait_decision(5), 2)
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()

    def test_corrupt_table_rejected(self):
        self.path.write_bytes(b'invalid')
        with self.assertRaises(ValueError):
            DecisionTable(self.core, self.path)

    def test_repeated_requests_and_full_slot(self):
        payload = 'x' * 4096
        self.train([(payload, 2)])
        with FastChannel(self.name, core=self.core) as channel:
            for _ in range(1000):
                channel.write_input(payload.encode())
                self.assertIsNone(channel.read_decision())
                self.assertEqual(channel.process(), 1)
                self.assertEqual(channel.process(), 0)
                self.assertEqual(channel.read_decision(), 2)


if __name__ == '__main__':
    unittest.main()
