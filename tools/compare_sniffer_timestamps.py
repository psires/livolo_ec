#!/usr/bin/env python3
"""Compare byte-identical capture candidates and estimate clock offset/drift.

Identical retransmissions can be paired ambiguously. Residuals are retained,
including large outliers; neither capture is assumed lossless. Absolute host
clock anchors must be within --window seconds for candidate matching.
"""
import argparse
import collections
import json
from pathlib import Path
import statistics

from decode_ec import decode_frame, read_pcapng


def packets(path):
    """Read MAC frames and capture times.

    @param path Input pcapng path supported by the EC decoder.
    @return List of packet dictionaries.
    """
    return [
        dict(n=n, t=t, raw=raw.hex(), length=len(raw))
        for n, t, raw, _ in read_pcapng(path)
    ]


def is_ec(packet):
    """Check the proprietary checksum without assuming a source identity.

    @param packet Packet dictionary with hexadecimal raw MAC bytes.
    @return True for a decodable EC packet with a valid checksum.
    """
    try:
        return decode_frame(bytes.fromhex(packet["raw"]))["checksum_ok"]
    except ValueError:
        return False


def compare(first, second, window):
    """Fit clock drift using 39-byte EC frames and retain all candidate residuals.

    @param first First capture's packet dictionaries.
    @param second Second capture's packet dictionaries.
    @param window Maximum absolute host-time difference in seconds.
    @return Comparison, coverage counts and complete candidate pair details.
    """
    index = collections.defaultdict(list)
    for packet in second:
        index[packet["raw"]].append(packet)
    pairs, used = [], set()
    for packet in first:
        candidates = [
            other
            for other in index[packet["raw"]]
            if other["n"] not in used and abs(other["t"] - packet["t"]) < window
        ]
        if candidates:
            other = min(candidates, key=lambda item: abs(item["t"] - packet["t"]))
            used.add(other["n"])
            pairs.append(
                dict(a=packet, b=other, difference_us=(packet["t"] - other["t"]) * 1e6)
            )
    reference = [p for p in pairs if p["a"]["length"] == 39 and is_ec(p["a"])]
    if len(reference) < 3:
        raise ValueError("Need at least three matched 39-byte EC frames")
    origin = statistics.mean(p["b"]["t"] for p in reference)
    xs = [p["b"]["t"] - origin for p in reference]
    ys = [p["difference_us"] for p in reference]
    offset = statistics.mean(ys)
    denominator = sum(x * x for x in xs)
    if not denominator:
        raise ValueError("Reference frames must span time")
    drift = sum(x * (y - offset) for x, y in zip(xs, ys)) / denominator
    groups = collections.defaultdict(list)
    for pair in pairs:
        residual = pair["difference_us"] - offset - drift * (pair["b"]["t"] - origin)
        pair["residual_us"] = residual
        groups[pair["a"]["length"]].append(residual)
    start = max(first[0]["t"], second[0]["t"]) + window
    end = min(first[-1]["t"], second[-1]["t"]) - window
    if end <= start:
        raise ValueError("No conservative overlap window")
    a = [p for p in first if start <= p["t"] <= end and is_ec(p)]
    b = [p for p in second if start <= p["t"] <= end and is_ec(p)]
    common = sum(
        (
            collections.Counter(p["raw"] for p in a)
            & collections.Counter(p["raw"] for p in b)
        ).values()
    )
    return dict(
        first_packets=len(first),
        second_packets=len(second),
        matched=len(pairs),
        reference_pairs=len(reference),
        overlap_seconds=end - start,
        ec_first=len(a),
        ec_second=len(b),
        ec_common=common,
        reference_epoch=origin,
        offset_us=offset,
        drift_ppm=drift,
        by_length={
            str(length): dict(
                count=len(values),
                median_us=statistics.median(values),
                min_us=min(values),
                max_us=max(values),
            )
            for length, values in sorted(groups.items())
        },
        pairs=pairs,
    )


def main():
    """Write a reproducible comparison of two explicit capture paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--window", type=float, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.window <= 0:
        parser.error("window must be positive")
    result = compare(packets(args.first), packets(args.second), args.window)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "pairs"}, indent=2))


if __name__ == "__main__":
    main()
