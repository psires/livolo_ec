#!/usr/bin/env python3
"""Compare exact EC frame bytes in two overlapping passive captures.

Uses a one-second interior margin to avoid capture startup/stop and UART delay.
Counts repeated packets separately; does not infer absolute reception rates.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from decode_ec import read_pcapng, decode_frame


def main():
    """Compare exact EC packet counts within the overlapping capture interval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    frames = [list(read_pcapng(path)) for path in (args.first, args.second)]
    if any(not capture for capture in frames):
        parser.error("Both captures must contain frames")
    start = max(capture[0][1] for capture in frames) + 1
    end = min(capture[-1][1] for capture in frames) - 1
    if start >= end:
        parser.error("No useful capture overlap")
    counters, summaries = [], []
    for path, capture in zip((args.first, args.second), frames):
        counter, sources, invalid, other = Counter(), Counter(), 0, 0
        for _, timestamp, raw, _ in capture:
            if not start <= timestamp <= end:
                continue
            try:
                decoded = decode_frame(raw)
            except ValueError:
                other += 1
                continue
            counter[raw] += 1
            sources[decoded["source_wire"]] += 1
            invalid += not decoded["checksum_ok"]
        counters.append(counter)
        summaries.append(
            {
                "file": path.name,
                "ec_packets": sum(counter.values()),
                "invalid_ec_checksums": invalid,
                "other_packets": other,
                "sources": dict(sources),
            }
        )
    common = counters[0] & counters[1]
    result = {
        "overlap_seconds": round(end - start, 3),
        "captures": summaries,
        "identical_ec_packets": sum(common.values()),
        "only_first": sum((counters[0] - counters[1]).values()),
        "only_second": sum((counters[1] - counters[0]).values()),
        "note": "Exact bytes with multiplicity, host timestamp overlap; neither receiver is ground truth.",
    }
    print(json.dumps(result, indent=2))
    if not common or any(summary["invalid_ec_checksums"] for summary in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
