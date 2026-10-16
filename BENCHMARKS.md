# Benchmarks

Historical Windows reference: **1,000,000 decisions**, **1,493.9191 ns** average
(1.4939191 microseconds). The unchanged run is in
`benchmark-reference-windows.json`; publishing refreshes `benchmark-results.json`
on Windows or `benchmark-results-linux.json` on Linux.

| Model / System | Architecture | Avg Latency | Relative Speed |
| :--- | :--- | :--- | :--- |
| **Silicon Reflex (measured reference)** | C + XXH64 + mmap, prehashed input | **1.49 µs** | **1x baseline** |
| Jev (illustrative estimate) | Tiny classifier | ~50-100 µs | ~34-67x slower if assumptions hold |
| Laya (illustrative estimate) | Optimized LLM | ~500-1,000 µs | ~336-671x slower if assumptions hold |
| Standard LLM (illustrative estimate) | Transformer, 7B+ | ~50,000+ µs | ~33,557x+ slower if assumptions hold |

**Jev/Laya and standard LLM figures are user-supplied estimates, not measurements
or verified model specifications.** They illustrate optimized classifier/LLM
scenarios, not evidence for the named models' actual performance. Relative
factors divide the estimates by 1.49 µs. Model inference, text generation, and
exact-match label lookup perform different work: this is not an apples-to-apples
benchmark. No independent source is claimed for these estimates.

## Methodology

- Python 3.12.10, Windows 11 x64, MSVC /O2; CPU signature appears in JSON.
- 2^20 slots (10 MiB), **three cache-hot sample keys**, CPU 0 affinity.
- Precomputed hash -> Python mmap write -> synchronous ctypes C call -> mmap read.
- This is SAME-PROCESS timing, not separate consumer-process scheduling latency.
- Native lookup separately measured about 1.65 ns for that tiny working set;
  that figure is not request-to-response latency.
- Checksums match. Timers surround batches, not individual decisions. Median/max
  fields are batch averages, not per-decision percentiles.
- UTF-8 encoding, fresh-input hashing, the bridge lock, startup, LLM fallback,
  cold caches, and cross-process scheduling are excluded.
- Historical power settings were not captured. No fixed-frequency claim is made.

```sh
python -m pytest -q
python benchmark_physics.py --iterations 1000000 --report benchmark-results.json
```

Build the library and train first. Use representative data and measure the actual
deployment before making production claims. IPC tests prove basic correctness,
not cross-process latency. The tagline is not a sub-2µs guarantee. Publishing does
not fail merely because another machine exceeds the historical reference.
