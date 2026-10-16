"""Offline supervised lookup-table builder. No training on the critical path."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import ctypes as C
import json
import struct
from shared_memory import HEADER, MAGIC, ROOT, load_core


def build_decision_table(dataset: list[tuple[str, int]], *, bits=20,
                         output=ROOT / 'decision_table.bin'):
    if not 1 <= bits <= 26:
        raise ValueError('bits must be 1..26 (10 bytes per slot)')
    core = load_core()
    votes = defaultdict(Counter)
    for text, decision in dataset:
        if not isinstance(text, str) or type(decision) is not int or decision not in (0, 1, 2):
            raise ValueError('Each row must be (text, integer decision 0/1/2)')
        votes[text.encode('utf-8')][decision] += 1
    count = 1 << bits
    decisions = bytearray([255]) * count
    confidence = bytearray(count)
    fingerprints = bytearray(count * 8)
    winners = {}
    collisions = 0
    for data, counts in sorted(votes.items()):
        key = core.core_hash(data, len(data))
        slot = key & (count - 1)
        label, hits = min(counts.items(), key=lambda item: (-item[1], item[0]))
        certainty = round(255 * hits / sum(counts.values()))
        candidate = (certainty, sum(counts.values()), -key)
        if slot in winners:
            collisions += 1
            if candidate <= winners[slot]:
                continue
        winners[slot] = candidate
        decisions[slot], confidence[slot] = label, certainty
        struct.pack_into('<Q', fingerprints, slot * 8, key)
    with Path(output).open('wb') as stream:
        stream.write(HEADER.pack(MAGIC, count))
        stream.write(decisions)
        stream.write(confidence)
        stream.write(fingerprints)
    return {'slots': count, 'unique_inputs': len(votes), 'collisions': collisions,
            'stored_inputs': len(winners), 'bytes': 16 + count * 10}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', nargs='?', help='JSON array of [text, decision] pairs')
    parser.add_argument('--bits', type=int, default=20)
    parser.add_argument('--output', type=Path, default=ROOT / 'decision_table.bin')
    args = parser.parse_args()
    rows = json.loads(Path(args.dataset).read_text('utf-8')) if args.dataset else [
        ('example:hold', 0), ('example:buy', 1), ('example:sell', 2)]
    print(json.dumps(build_decision_table(rows, bits=args.bits, output=args.output), indent=2))
