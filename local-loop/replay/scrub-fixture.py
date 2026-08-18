#!/usr/bin/env python3
"""Replace operator badge numbers in a replay fixture with stable surrogates.

Archived AVL carries operatorID.designator, which is an MTA employee badge number. A fixture that is
committed to the repository puts that number in git history permanently, so it is replaced here.

Substitution is textual, never a JSON round-trip. make-replay-fixture.py deliberately writes the
archive's envelope string through byte-for-byte, because re-encoding could change key order or number
formatting and a fixture's whole purpose is that two runs see identical input. Only the designator's
characters change; every other byte on the line is preserved.

The mapping is a CRC32 of the original, so scrubbing the same input twice gives the same output, and
distinct badges stay distinct - the fixture keeps the same number of operators it started with.

Inference is unaffected in a local loop: operator assignment needs UTS run data, which a local bundle
does not have. That is the same gap that makes three of the four trip-selection likelihoods return a
constant. Verify with one determinism run before and after if you depend on it.

Usage:
  scrub-fixture.py <in.jsonl> <out.jsonl>
"""
import re
import sys
import zlib

# operatorID's designator only. runID has a designator too, and it is a run number, not a person.
OPERATOR_DESIGNATOR = re.compile(r'("operatorID"\s*:\s*\{[^}]*?"designator"\s*:\s*")([^"]*)(")')


def surrogate(badge):
    """A stable six-digit stand-in. Distinct badges give distinct surrogates for realistic inputs."""
    return str(100000 + zlib.crc32(badge.encode()) % 900000)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip())
    src, dst = sys.argv[1], sys.argv[2]

    mapping = {}

    def replace(m):
        badge = m.group(2)
        if badge not in mapping:
            mapping[badge] = surrogate(badge)
        return m.group(1) + mapping[badge] + m.group(3)

    lines = 0
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            fout.write(OPERATOR_DESIGNATOR.sub(replace, line))
            lines += 1

    print("%s -> %s" % (src, dst))
    print("  %d lines, %d operator designators replaced" % (lines, len(mapping)))
    if len(set(mapping.values())) != len(mapping):
        sys.exit("  ERROR: surrogates collided; distinct operators would be merged")
    for badge in sorted(mapping):
        print("    %-10s -> %s" % ("*" * len(badge), mapping[badge]))


if __name__ == "__main__":
    main()
