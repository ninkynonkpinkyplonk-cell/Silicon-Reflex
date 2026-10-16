"""Fail-closed ctypes bridge. No network or file I/O after initialization.

Call initialize_reflex() at worker startup, then reflex_decide(text).
The first reflex_decide() also initializes automatically if necessary.
Do not mix this owner with shared_memory.DecisionTable in the same process.
"""
import atexit
import ctypes as C
import logging
import math
import mmap
import os
from pathlib import Path
import struct
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
_LOG = logging.getLogger(__name__)
_OWNER_LOCK = threading.RLock()
_OWNERS = {}


class ReflexBridge:
    def __init__(self, engine_dir=ROOT, *, min_confidence=0.90, cpu=None,
                 pin_cpu=True, table_path=None):
        if not isinstance(min_confidence, (int, float)) or not 0 <= min_confidence <= 1:
            raise ValueError('min_confidence must be 0..1')
        if cpu is not None and (type(cpu) is not int or cpu < 0):
            raise ValueError('cpu must be a nonnegative integer')
        self.root = Path(engine_dir).resolve()
        self.path = Path(table_path).resolve() if table_path else self.root / 'decision_table.bin'
        self.threshold = math.ceil(min_confidence * 255)
        self._lock = threading.RLock()
        self.ready = False
        self.error = None
        self.cpu = None
        self.slots = 0
        self._mapping = self._anchor = self._core = None
        self._shared = None
        self._restore_affinity = None
        self._owns = False
        self._key = str(self.root / ('decision_core.dll' if os.name == 'nt' else 'libdecision_core.so'))
        try:
            with _OWNER_LOCK:
                if self._key in _OWNERS:
                    raise RuntimeError('One bridge owner per native library/process is supported')
                if os.name != 'nt' and not sys.platform.startswith('linux'):
                    raise OSError('Supported: Windows x64 and Linux x86_64')
                self._load_table()
                self._core = C.CDLL(self._key)
                self._core.core_bind.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint64]
                self._core.core_bind.restype = C.c_int
                self._core.core_unbind.argtypes = []
                self._core.core_unbind.restype = None
                self._core.core_hash.argtypes = [C.c_void_p, C.c_size_t]
                self._core.core_hash.restype = C.c_uint64
                self._core.core_lookup.argtypes = [C.c_uint64, C.c_uint8]
                self._core.core_lookup.restype = C.c_uint8
                address = C.addressof(self._anchor) + self._prefix + 16
                if not self._core.core_bind(address, address + self.slots,
                                             address + 2 * self.slots, self.slots):
                    raise ValueError('Native table binding rejected')
                self._owns = True
                _OWNERS[self._key] = self
                if pin_cpu:
                    self._pin_cpu(cpu)
                # Touch every table page during startup, outside the decision path.
                self._warmup = sum(self._view[i] for i in range(0, len(self._view), mmap.PAGESIZE))
                self.ready = True
        except (OSError, ValueError, RuntimeError, OverflowError, MemoryError) as error:
            self.error = str(error)
            self.close()
            _LOG.warning('Silicon Reflex unavailable; using LLM fallback: %s', error)

    def _load_table(self):
        with self.path.open('rb') as stream:
            header = stream.read(16)
            if len(header) != 16:
                raise ValueError('Truncated table header')
            magic, self.slots = struct.unpack('<8sQ', header)
            if magic != b'SRFX0001' or self.slots < 2 or self.slots & (self.slots - 1):
                raise ValueError('Invalid table header')
            size = os.fstat(stream.fileno()).st_size
            if size != 16 + self.slots * 10 or self.slots > 2**26:
                raise ValueError('Invalid table length or unsupported table size')
            # Align uint64 fingerprints even for legacy two-slot tables.
            self._prefix = (-16 - 2 * self.slots) & 7
            length = size + self._prefix
            self.mapping_name = 'srfx_table_' + uuid.uuid4().hex
            if os.name == 'nt':
                self.mapping_name = 'Local\\' + self.mapping_name
                self._mapping = mmap.mmap(-1, length, tagname=self.mapping_name,
                                          access=mmap.ACCESS_WRITE)
                self._view = memoryview(self._mapping)
            else:
                from multiprocessing.shared_memory import SharedMemory
                self._shared = SharedMemory(name=self.mapping_name, create=True, size=length)
                self._view = self._shared.buf
            stream.seek(0)
            destination = self._view[self._prefix:self._prefix + size]
            try:
                offset = 0
                while offset < size:
                    part = destination[offset:]
                    try:
                        received = stream.readinto(part)
                    finally:
                        part.release()
                    if not received:
                        raise ValueError('Table changed while loading')
                    offset += received
            finally:
                destination.release()
            start = self._prefix + 16
            data = self._view[start:start + self.slots]
            try:
                if any(value not in (0, 1, 2, 255) for value in data):
                    raise ValueError('Invalid decision values')
            finally:
                data.release()
            self._anchor = C.c_char.from_buffer(self._view)

    def _pin_cpu(self, cpu):
        if os.name != 'nt':
            original = os.sched_getaffinity(0)
            selected = min(original) if cpu is None else cpu
            if selected not in original:
                raise ValueError('CPU is outside the allowed affinity mask')
            os.sched_setaffinity(0, {selected})
            self._restore_affinity = lambda: os.sched_setaffinity(0, original)
        else:
            kernel = C.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = C.c_void_p
            kernel.GetProcessAffinityMask.argtypes = [C.c_void_p, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)]
            kernel.GetProcessAffinityMask.restype = C.c_int
            kernel.SetProcessAffinityMask.argtypes = [C.c_void_p, C.c_size_t]
            kernel.SetProcessAffinityMask.restype = C.c_int
            process = kernel.GetCurrentProcess()
            original, system = C.c_size_t(), C.c_size_t()
            if not kernel.GetProcessAffinityMask(process, C.byref(original), C.byref(system)):
                raise C.WinError(C.get_last_error())
            selected = (original.value & -original.value).bit_length() - 1 if cpu is None else cpu
            if selected >= C.sizeof(C.c_size_t) * 8 or not (original.value & (1 << selected)):
                raise ValueError('CPU is outside the current processor-group affinity mask')
            if not kernel.SetProcessAffinityMask(process, 1 << selected):
                raise C.WinError(C.get_last_error())
            self._restore_affinity = lambda: kernel.SetProcessAffinityMask(process, original.value)
        self.cpu = selected

    def decide(self, input_text: str) -> int:
        if not isinstance(input_text, str) or len(input_text) > 4096:
            return -1
        with self._lock:
            if not self.ready:
                return -1
            try:
                data = input_text.encode('utf-8')
                if len(data) > 4096:
                    return -1
                key = self._core.core_hash(data, len(data))
                result = self._core.core_lookup(key, self.threshold)
                return result if result <= 2 else -1
            except (UnicodeError, ValueError, OSError):
                return -1

    def close(self):
        with self._lock, _OWNER_LOCK:
            self.ready = False
            if self._owns:
                self._core.core_unbind()
                _OWNERS.pop(self._key, None)
                self._owns = False
            self._anchor = None
            view = getattr(self, '_view', None)
            if view is not None:
                view.release()
                self._view = None
            if self._mapping is not None:
                self._mapping.close()
                self._mapping = None
            if self._shared is not None:
                self._shared.close()
                self._shared.unlink()
                self._shared = None
            if self._restore_affinity is not None:
                try:
                    self._restore_affinity()
                except OSError:
                    pass
                self._restore_affinity = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_default = None
_attempted = False
_DEFAULT_LOCK = threading.RLock()


def initialize_reflex(**options):
    """Call once at Python worker startup; errors leave a safe fallback owner."""
    global _default, _attempted
    with _DEFAULT_LOCK:
        if not _attempted:
            _attempted = True
            try:
                options.setdefault('engine_dir', os.environ.get('SILICON_REFLEX_ROOT', ROOT))
                options.setdefault('min_confidence', float(os.environ.get('REFLEX_MIN_CONFIDENCE', '0.90')))
                if 'REFLEX_CPU' in os.environ:
                    options.setdefault('cpu', int(os.environ['REFLEX_CPU']))
                _default = ReflexBridge(**options)
            except (ValueError, TypeError, OSError) as error:
                _LOG.warning('Invalid Reflex configuration; using LLM fallback: %s', error)
        return _default


def reflex_decide(input_text: str) -> int:
    engine = initialize_reflex()
    return engine.decide(input_text) if engine is not None else -1


def shutdown_reflex():
    global _default, _attempted
    with _DEFAULT_LOCK:
        if _default is not None:
            _default.close()
            _default = None
        _attempted = False


atexit.register(shutdown_reflex)
