# What Silicon Reflex will not do

- **No general intelligence.** It classifies only stored exact byte sequences.
  Unseen paraphrases, capitalization, whitespace, and punctuation changes do not
  generalize. Unknown inputs return the LLM fallback sentinel.
- **No complex reasoning.** This is a reflex table, not a reasoning engine or
  text generator. Confidence is agreement among training labels, not calibrated
  probability of correctness.
- **Hardware specific.** Measured on Windows x86_64. A Linux x86_64 source port
  and CI are supplied but were not run on the Windows workstation. The default
  build does NOT require AVX2. ARM/macOS need porting, recompilation, and testing;
  a Windows DLL is not portable to them.
- **Memory bound.** The default 2^20 slots consume exactly **10 MiB**, plus the
  16-byte header and runtime overhead. The sample has **3 occupied records**.
  Each slot costs 10 bytes regardless of occupancy. Training/loading need extra
  memory. Pages are preloaded but not locked against OS paging.
- **Collision limits.** One full-hash fingerprint per slot. Index collisions
  lower coverage: the highest-confidence winner stays and displaced keys
  abstain. Lookup remains **O(1)**, never O(n), because there are no chains or
  probes. More misses increase overall LLM-fallback latency. Exact XXH64
  collisions remain possible; XXH64 is not a cryptographic authentication check.
- **No hard real-time SLA.** Scheduling, interrupts, thermals, power saving,
  caches, Python/FFI overhead, and contention influence latency. Reference timing
  covers a tiny cache-hot working set with prehashed inputs.
- **Limited IPC.** One trusted producer/consumer, one in-flight request, 4096 input
  bytes. Not a security boundary or crash-recovery protocol. Recreate channels
  after producer restart. Linux/Python 3.12 needs a common multiprocessing parent
  for resource-tracker lifetime management.
- **One native table owner.** Do not mix a bridge and a separately bound low-level
  DecisionTable in one process. Stop callers before shutdown or replacing tables.
  Affinity restricts the Python worker process and is restored on close.
- **No implicit action policy.** Labels 0/1/2 need application-specific handlers.
  -1 means continue the existing LLM path. No guessed label routing or trade
  execution is added to Electron.
