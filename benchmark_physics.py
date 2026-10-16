"""Measured native lookup vs Python + named mmap + synchronous C round trip."""
import argparse
import ctypes as C
import gc
import json
import platform
import os
import statistics
import time
import uuid
from shared_memory import DecisionTable, FastChannel, ROOT, load_core


def pin_cpu(cpu=None):
    if os.name != 'nt':
        original = os.sched_getaffinity(0)
        selected = min(original) if cpu is None else cpu
        if selected not in original:
            raise ValueError('CPU is outside the allowed process affinity mask')
        os.sched_setaffinity(0, {selected})
        return None, None, original, selected
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = C.c_void_p
    kernel.GetProcessAffinityMask.argtypes = [C.c_void_p, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [C.c_void_p, C.c_size_t]
    process = kernel.GetCurrentProcess()
    allowed, system = C.c_size_t(), C.c_size_t()
    if not kernel.GetProcessAffinityMask(process, C.byref(allowed), C.byref(system)):
        raise C.WinError(C.get_last_error())
    selected = (allowed.value & -allowed.value) if cpu is None else 1 << cpu
    if selected & allowed.value != selected:
        raise ValueError('CPU is outside the allowed process affinity mask')
    if not kernel.SetProcessAffinityMask(process, selected):
        raise C.WinError(C.get_last_error())
    return kernel, process, allowed.value, selected.bit_length() - 1


def benchmark(iterations=1_000_000, cpu=None):
    if iterations < 100:
        raise ValueError('Use at least 100 iterations')
    core = load_core()
    table = DecisionTable(core)
    # Reuse actual stored fingerprints; avoid measuring only missing-input lookup.
    keys = [table.fingerprints[i] for i in range(table.count)
            if table.decisions[i] <= 2][:65536]
    if not keys:
        table.close()
        raise ValueError('Train a nonempty decision table first')
    native_keys = (C.c_uint64 * len(keys))(*keys)
    checksum = C.c_uint64()
    kernel, process, original, selected = pin_cpu(cpu)
    was_enabled = gc.isenabled()
    try:
        with FastChannel('Local\\SiliconReflexBench-' + uuid.uuid4().hex, core=core) as channel:
            for i in range(10000):
                channel.write_hash(keys[i % len(keys)])
                channel.process()
                channel.read_decision()
            gc.disable()
            native_ns = core.core_benchmark(native_keys, len(keys), iterations, 0, C.byref(checksum))
            samples, total, consumed = [], 0, 0
            while consumed < iterations:
                batch = min(10000, iterations - consumed)
                start = time.perf_counter_ns()
                for i in range(batch):
                    channel.write_hash(keys[(consumed + i) % len(keys)])
                    channel.process()
                    result = channel.read_decision()
                    if result is None:
                        raise RuntimeError('Synchronous decision missing')
                    total += result
                elapsed = time.perf_counter_ns() - start
                samples.append((elapsed / batch, batch))
                consumed += batch
            average = sum(ns * n for ns, n in samples) / iterations
            if total != checksum.value:
                raise RuntimeError('Native and Python decision checksums differ')
            return {
                'python': platform.python_version(), 'os': platform.platform(),
                'processor': platform.processor(),
                'iterations': iterations, 'cpu': selected, 'table_bytes': table.count * 10,
                'working_keys': len(keys), 'native_lookup_ns': native_ns,
                'python_mmap_sync_roundtrip_ns': average,
                'batch_average_median_ns': statistics.median(ns for ns, _ in samples),
                'batch_average_max_ns': max(ns for ns, _ in samples),
                'under_10us_average': average < 10000,
                'native_checksum': checksum.value, 'python_checksum': total,
                'mode': 'same-process ctypes + named mmap; not two-process IPC latency',
            }
    finally:
        if was_enabled:
            gc.enable()
        if kernel is None:
            os.sched_setaffinity(0, original)
        else:
            kernel.SetProcessAffinityMask(process, original)
        table.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', type=int, default=1_000_000)
    parser.add_argument('--cpu', type=int)
    parser.add_argument('--report', type=str)
    args = parser.parse_args()
    result = benchmark(args.iterations, args.cpu)
    text = json.dumps(result, indent=2)
    print(text)
    if args.report:
        (ROOT / args.report).write_text(text + '\n', encoding='utf-8')
