"""Named shared-memory SPSC channel. No filesystem IPC on the hot path."""
import ctypes as C
import mmap
import os
import struct
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAPACITY = 4096
HEADER = struct.Struct('<8sQ')
MAGIC = b'SRFX0001'


def load_core(path=None):
    if os.name != 'nt' and not __import__('sys').platform.startswith('linux'):
        raise OSError('Supported systems: Windows x64 and Linux x86_64')
    dll = C.CDLL(str(path or ROOT / ('decision_core.dll' if os.name == 'nt' else 'libdecision_core.so')))
    signatures = {
        'core_channel_size': ([], C.c_uint32),
        'core_hash': ([C.c_void_p, C.c_size_t], C.c_uint64),
        'core_bind': ([C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint64], C.c_int),
        'core_unbind': ([], None),
        'core_lookup': ([C.c_uint64, C.c_uint8], C.c_uint8),
        'core_publish': ([C.c_void_p, C.c_uint64], C.c_int),
        'core_process': ([C.c_void_p], C.c_int),
        'core_ready': ([C.c_void_p, C.c_uint64], C.c_int),
        'core_benchmark': ([C.c_void_p, C.c_uint32, C.c_uint64, C.c_uint8,
                            C.POINTER(C.c_uint64)], C.c_double),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return dll


class DecisionTable:
    """One immutable table per process/DLL. Load before starting requests."""
    def __init__(self, core, path=ROOT / 'decision_table.bin'):
        self.core = core
        raw = Path(path).read_bytes()  # Startup only.
        if len(raw) < HEADER.size:
            raise ValueError('Truncated table header')
        magic, count = HEADER.unpack_from(raw)
        if magic != MAGIC or count < 2 or count & (count - 1):
            raise ValueError('Invalid table header')
        if len(raw) != HEADER.size + count * 10:
            raise ValueError('Invalid table length')
        self.count = count
        self.decisions = (C.c_uint8 * count).from_buffer_copy(raw[16:16 + count])
        self.confidence = (C.c_uint8 * count).from_buffer_copy(raw[16 + count:16 + count * 2])
        self.fingerprints = (C.c_uint64 * count).from_buffer_copy(raw[16 + count * 2:])
        if any(d not in (0, 1, 2, 255) for d in self.decisions):
            raise ValueError('Invalid decisions')
        if not core.core_bind(self.decisions, self.confidence, self.fingerprints, count):
            raise ValueError('Cannot bind table')

    def close(self):
        # Stop all users before closing. Multiple simultaneous binds unsupported.
        self.core.core_unbind()


class FastChannel:
    def __init__(self, name='Local\\SiliconReflex', *, core=None, create=True,
                 threshold=0):
        if not 0 <= threshold <= 255:
            raise ValueError('threshold must be 0..255')
        self.core = core or load_core()
        self._shared = None
        self._creator = create
        if os.name == 'nt':
            self.mapping = mmap.mmap(-1, self.core.core_channel_size(), tagname=name,
                                     access=mmap.ACCESS_WRITE)
            self.view = memoryview(self.mapping)
        else:
            from multiprocessing.shared_memory import SharedMemory
            safe_name = 'srfx_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:32]
            self._shared = SharedMemory(name=safe_name, create=create,
                                        size=self.core.core_channel_size() if create else 0)
            self.mapping = None
            self.view = self._shared.buf
        self._anchor = C.c_char.from_buffer(self.view)
        self.address = C.addressof(self._anchor)
        self.sequence = 0
        self.pending = False
        self.threshold = threshold
        if create:
            # Only the creator may initialize, before the consumer attaches.
            self.view[:] = b'\0' * len(self.view)

    def _begin(self):
        if self.pending:
            raise RuntimeError('Previous decision has not been consumed')
        self.sequence += 1
        if self.sequence >= 2**63:
            raise OverflowError('Recreate channel before sequence rollover')

    def _publish(self, mode):
        self.view[140] = mode
        self.view[141] = self.threshold
        if not self.core.core_publish(self.address, self.sequence):
            raise RuntimeError('Channel already has an outstanding request')
        self.pending = True

    def write_input(self, data: bytes):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError('data must be a contiguous byte buffer')
        source = memoryview(data).cast('B')
        if len(source) > CAPACITY:
            raise ValueError('Input exceeds preallocated 4096-byte slot')
        self._begin()
        self.view[192:192 + len(source)] = source
        struct.pack_into('<I', self.view, 136, len(source))
        self._publish(0)

    def write_hash(self, key):
        if not 0 <= key < 2**64:
            raise ValueError('hash must be uint64')
        self._begin()
        struct.pack_into('<QI', self.view, 128, key, 0)
        self._publish(1)

    def process(self):
        """Synchronous DLL execution; a separate consumer may call this instead."""
        return self.core.core_process(self.address)

    def read_decision(self):
        """Nonblocking: None until the published request has completed."""
        if not self.pending or not self.core.core_ready(self.address, self.sequence):
            return None
        self.pending = False
        return self.view[142]

    def wait_decision(self, timeout=1.0):
        deadline = time.perf_counter() + timeout
        while True:
            result = self.read_decision()
            if result is not None:
                return result
            if time.perf_counter() >= deadline:
                raise TimeoutError('Consumer did not respond; request remains pending')
            time.sleep(0)

    def close(self):
        self.view.release()
        del self._anchor
        if self._shared is not None:
            self._shared.close()
            if self._creator:
                try:
                    self._shared.unlink()
                except FileNotFoundError:
                    pass
        else:
            self.mapping.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
