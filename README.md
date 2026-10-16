# Silicon Reflex: Physics-Limit AI Decision Engine

**Sub-2µs decision-making using zero-model, branchless C logic.**

Observed in the original cache-hot, prehashed Windows benchmark: **1.49 µs**
average across 1,000,000 same-process Python + mmap + C round trips. This is a
prototype result, not a latency guarantee or an LLM inference benchmark.
See [BENCHMARKS.md](BENCHMARKS.md) for methodology and current results.

Silicon Reflex returns learned labels 0/1/2 for exact UTF-8 inputs. Unknown,
displaced, or low-confidence inputs return **-1** through the Prism bridge so
the existing LLM pipeline can handle them. It does not generate text.

## Features

- XXH64 hashing, contiguous uint8 decisions, masked O(1) lookup.
- Branchless native label/confidence selection with full-hash collision protection.
- Named mmap IPC on Windows; POSIX named shared memory on Linux.
- Table copied into named shared memory once; no file access per decision.
- Automatic CPU affinity during worker initialization; restored on shutdown.
- No network call, neural network, C allocation, or disk write in the lookup path.
- Explicit LLM fallback and a thread-safe Python bridge.

## Windows installation

Use **64-bit Python 3.12**, plus Visual Studio 2022 Build Tools with **Desktop
development with C++**, or a 64-bit MinGW GCC on PATH. The build script locates
MSVC through vswhere. AVX2 is not required by the default compiler settings.

```powershell
git clone https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
cd silicon-reflex
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
.\build_dll.bat
python trainer.py
python -m pytest -q
python benchmark_physics.py --iterations 1000000 --report benchmark-results.json
```

## Linux installation

A Linux x86_64 port uses GCC/Clang, POSIX shared memory, monotonic timing, and
Linux affinity. It was not executed on the Windows workstation; require the
Linux CI job to pass before claiming a verified Linux release. ARM/macOS are
not supported release targets.

```sh
# Install Python 3.12, venv support, and a C compiler via your distribution.
git clone https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
cd silicon-reflex
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
sh build_dll.sh
python trainer.py
python -m pytest -q
python benchmark_physics.py --iterations 1000000 --report benchmark-results-linux.json
```

Linux/Python 3.12 channel processes must share a multiprocessing parent for
resource-tracker lifetime management. Unrelated-process attachment needs an
explicit lifetime manager and is not supported in this release.

## Train a table

```python
from trainer import build_decision_table
build_decision_table([('example:hold', 0), ('example:buy', 1), ('example:sell', 2)])
```

CLI: `python trainer.py dataset.json --bits 20`, where the dataset is a JSON
array of [text, integer decision] pairs. The sample is not a trading strategy.
Confidence is majority-label agreement, quantized to 0..255. Index collisions
prefer higher confidence, then sample count, then lower full hash. Label ties
prefer the lower label. Displaced keys abstain; exact 64-bit hash collisions
remain possible. Keep private datasets and generated tables out of Git.

Little-endian binary layout: 8-byte SRFX0001 magic, uint64 slot count,
uint8 decisions[count], uint8 confidence[count], uint64 fingerprints[count].
Default **2^20 slots / 10 MiB**, independent of occupancy. The sample occupies
only three slots. Exact matching does not normalize case, whitespace, punctuation
or Unicode. More varied real inputs generally need the standard LLM fallback.

## Prism / Vanta bridge

`prism/reflex_bridge.py` loads the DLL/.so with ctypes, validates and copies the
table into named shared memory, warms its pages, and pins the Python worker to
one allowed CPU. Default minimum confidence is **0.90**.

```python
from prism.reflex_bridge import initialize_reflex, reflex_decide, shutdown_reflex

initialize_reflex()  # Call at Python worker startup.
label = reflex_decide('example:buy')  # 1; unknown/unavailable/low confidence -> -1
if label == -1:
    pass  # Continue the existing standard LLM call here.
else:
    pass  # Dispatch through your application's label 0/1/2 handler.
shutdown_reflex()
```

`initialize_reflex(min_confidence=0.95, cpu=2)` selects settings.
`SILICON_REFLEX_ROOT` identifies the native library/table directory;
`REFLEX_MIN_CONFIDENCE` and `REFLEX_CPU` are supported too.
Missing/corrupt files or affinity failures disable the shortcut. The first
decision initializes automatically if startup initialization was omitted; exclude
that call from steady-state measurements. Fix configuration and restart, or call
shutdown before retrying a failed initialization.

The main Vanta workspace has a small Python adapter loading this sibling release.
It initializes when imported by a **Python worker**; it never runs in Electron's
renderer. The JavaScript LLM pipeline is not silently rerouted: labels 0/1/2 have
application-specific meanings that must not be guessed. The text bridge includes
UTF-8 encoding and locking, so its latency is not the historical prehashed figure.

One table owner per native library/process is supported. Do not mix a bridge
with a separately bound DecisionTable in the same process. Stop calls before
shutdown or table replacement. CPU affinity applies to the Python worker, not
the Electron UI, and Windows uses the current processor group. Preloading does
not lock pages against OS paging. Python still creates small interpreter objects.

## Named IPC

```python
from shared_memory import load_core, DecisionTable, FastChannel
core = load_core()
table = DecisionTable(core)
with FastChannel(name='Local\\MyReflex', core=core) as channel:
    channel.write_input(b'example:buy')
    channel.process()
    print(channel.read_decision())  # 1; C-level abstain is 255
table.close()
```

For two-process IPC, create the channel first and attach the consumer using
`create=False`. The consumer binds its own table and calls process(); the producer
uses nonblocking read_decision() or yielding wait_decision(timeout). One producer,
one consumer, one outstanding request, max 4096 input bytes. Never reinitialize
a live channel; recreate it after producer restart. This is trusted-process IPC,
not an authentication boundary. Input bytes copy once into preallocated storage;
memoryviews avoid intermediate payload copies, not all copying.

## Reproducibility and publishing

Select Windows Best performance / High performance and record power/thermal
settings. Disable CPU power saving only on a controlled machine where permitted.
Affinity reduces migration, not interrupts/context switches. No real-time SLA.

```powershell
# In this standalone folder, after Git identity/authentication are configured:
git init -b main
git remote add origin https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
.\publish.bat --check  # Tests and benchmark; no staging, commit or push.
.\publish.bat          # Verify, benchmark, stage all non-ignored changes, commit, push.
```

Linux: `sh publish.sh --check` or `sh publish.sh`. First build and train. Scripts
require the exact standalone repo root, expected origin, and branch main. No force push. Any failed check stops publishing. Scripts do
not create a remote repository, change visibility, or create a GitHub Release/tag.
The public remote must already exist and Git authentication must work.

See [DISCLAIMER.md](DISCLAIMER.md) and [NOTICE](NOTICE). Original source is MIT;
the vendored xxHash header retains its BSD-2-Clause license.

References: [Python mmap](https://docs.python.org/3.12/library/mmap.html),
[Windows affinity](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setprocessaffinitymask),
[xxHash](https://xxhash.com/).

## Local identity and push troubleshooting

Windows publish.bat checks repository-local user.name/user.email, sets a missing
name to Bloug, and prompts for a missing email (or uses REFLEX_GIT_EMAIL). It
never writes global Git identity and never stores your email in source files.
The Windows publisher corrects origin and its push URL to the linked
ninkynonkpinkyplonk-cell/Silicon-Reflex repository. Existing staged files are
accepted; git add --all stages all non-ignored changes. Both tests and benchmark
failures abort. A missing local history can adopt the existing remote main as
its parent without overwriting the working tree/index. No force push is used.

A rejected push remains an error: authentication may require gh auth login and
gh auth setup-git. Diverged branches need a reviewed merge/rebase. On failure,
the batch file pauses unless REFLEX_NO_PAUSE is set, and returns a nonzero exit.
Use publish.bat --check to run only tests and refresh benchmark-results.json.

