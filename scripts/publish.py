"""Fail-fast verification and explicit release-file publishing. No force push."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    '.gitignore', '.gitattributes', 'README.md', 'BENCHMARKS.md', 'DISCLAIMER.md',
    'LICENSE', 'NOTICE', 'requirements-dev.txt', 'pytest.ini', 'decision_core.c',
    'build_dll.bat', 'build_dll.sh', 'shared_memory.py', 'trainer.py',
    'benchmark_physics.py', 'benchmark-reference-windows.json', 'vendor/xxhash.h', 'test_reflex.py',
    'test_bridge.py', 'test_publish.py', 'prism/__init__.py', 'prism/reflex_bridge.py', 'publish.bat',
    'publish.sh', 'scripts/publish.py', '.github/workflows/ci.yml',
]
REMOTES = {'https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex',
           'git@github.com:ninkynonkpinkyplonk-cell/Silicon-Reflex',
           'ssh://git@github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex'}


def run(*args, capture=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def preflight():
    top = Path(run('git', 'rev-parse', '--show-toplevel', capture=True)).resolve()
    if top != ROOT:
        raise RuntimeError('Run git init -b main in the standalone silicon-reflex folder first')
    remote = run('git', 'remote', 'get-url', 'origin', capture=True).rstrip('/')
    if remote.endswith('.git'):
        remote = remote[:-4]
    if remote not in REMOTES:
        raise RuntimeError('origin must target github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex')
    # Also reject a distinct push URL that could redirect publishing elsewhere.
    push_urls = run('git', 'remote', 'get-url', '--push', '--all', 'origin', capture=True).splitlines()
    for url in push_urls:
        normalized = url.rstrip('/')
        if normalized.endswith('.git'):
            normalized = normalized[:-4]
        if normalized not in REMOTES:
            raise RuntimeError('origin push URL must target github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex')
    if run('git', 'symbolic-ref', '--short', 'HEAD', capture=True) != 'main':
        raise RuntimeError('Publish only from branch main; switch branches explicitly')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Run tests/benchmark only; do not stage, commit or push')
    args = parser.parse_args()
    if not args.check:
        preflight()
    for name in FILES:
        if not (ROOT / name).is_file():
            raise RuntimeError('Missing release file: ' + name)
    # Runs the original six tests plus the integration tests; any failure stops us.
    run(sys.executable, '-m', 'pytest', '-q')
    report = 'benchmark-results.json' if os.name == 'nt' else 'benchmark-results-linux.json'
    run(sys.executable, 'benchmark_physics.py', '--iterations', '1000000', '--report', report)
    if args.check:
        print('Verification complete. No Git changes staged, committed or pushed.')
        return
    preflight()
    run('git', 'add', '--all')
    changed = run('git', 'diff', '--cached', '--name-only', capture=True)
    if changed:
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        run('git', 'commit', '-m', 'release: verify Silicon Reflex ' + stamp)
    run('git', 'push', '--set-upstream', 'origin', 'main')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Publish stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
