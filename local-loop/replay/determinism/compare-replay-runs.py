#!/usr/bin/env python3
"""Compare two replay captures and report whether inference was reproducible.

Usage: compare-replay-runs.py run-a.ndjson run-b.ndjson

Input is what obs_inferred.py writes: one NycQueuedInferredLocationBean per line, in arrival order.

Arrival order is deliberately ignored. The engine runs one stripe per vehicle, so a vehicle's own
records are ordered, but the stripes publish concurrently and the interleaving between vehicles
differs every run. Comparing raw files would therefore always fail, for a reason that does not
matter. Records are keyed on (vehicleId, recordTimestamp) instead, which is what a downstream
consumer actually joins on.

The output distinguishes three things, because they have different causes:

  missing / extra    a record one run produced and the other did not - the engine skipped a record
                     in one run only. Real nondeterminism, and the most serious kind.
  differing fields   the same record with a different value. Named per field, because some are
                     benign (a generated uuid) and some are not (inferredTripId, phase).
  duplicate keys     the same (vehicleId, recordTimestamp) twice in one run, which makes the
                     comparison ambiguous rather than wrong. Reported so it cannot pass silently.
"""
import collections
import json
import sys


def load(path):
    """-> {(vehicleId, recordTimestamp): bean}, plus the keys seen more than once."""
    by_key = {}
    dupes = collections.Counter()
    bad = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            bean = rec.get("NycQueuedInferredLocationBean", rec) if isinstance(rec, dict) else rec
            key = (bean.get("vehicleId"), bean.get("recordTimestamp"))
            if key in by_key:
                dupes[key] += 1
            by_key[key] = bean
    return by_key, dupes, bad


def flatten(bean, prefix=""):
    """Nested beans (managementRecord) compared field by field, not as one blob."""
    out = {}
    for k, v in sorted(bean.items()):
        name = prefix + k
        if isinstance(v, dict):
            out.update(flatten(v, name + "."))
        else:
            out[name] = v
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    a_path, b_path = sys.argv[1], sys.argv[2]

    a, a_dupes, a_bad = load(a_path)
    b, b_dupes, b_bad = load(b_path)

    print("run A : %-45s %d records" % (a_path, len(a)))
    print("run B : %-45s %d records" % (b_path, len(b)))
    if a_bad or b_bad:
        print("  unparsed lines: A=%d B=%d" % (a_bad, b_bad))
    if a_dupes or b_dupes:
        print("  WARNING duplicate (vehicleId, recordTimestamp) keys: A=%d B=%d"
              % (sum(a_dupes.values()), sum(b_dupes.values())))
        print("          the comparison below is ambiguous for those records")

    only_a = sorted(set(a) - set(b), key=lambda k: (str(k[0]), k[1] or 0))
    only_b = sorted(set(b) - set(a), key=lambda k: (str(k[0]), k[1] or 0))
    shared = set(a) & set(b)

    field_diffs = collections.Counter()
    examples = {}
    differing = set()
    for key in shared:
        fa, fb = flatten(a[key]), flatten(b[key])
        for name in sorted(set(fa) | set(fb)):
            va, vb = fa.get(name, "<absent>"), fb.get(name, "<absent>")
            if va != vb:
                field_diffs[name] += 1
                examples.setdefault(name, (key, va, vb))
                differing.add(key)

    print()
    print("only in A      : %d" % len(only_a))
    for k in only_a[:5]:
        print("    %s @ %s" % k)
    print("only in B      : %d" % len(only_b))
    for k in only_b[:5]:
        print("    %s @ %s" % k)
    print("in both        : %d" % len(shared))
    print("records differing in >=1 field : %d of %d" % (len(differing), len(shared)))

    if differing:
        # Per vehicle, and the earliest record that differs. Divergence cascades, so the earliest
        # timestamp is the one worth tracing; the totals only say how far it spread.
        totals = collections.Counter(k[0] for k in shared)
        diffs = collections.Counter(k[0] for k in differing)
        first = {}
        for veh, ts in sorted(differing, key=lambda k: (str(k[0]), k[1] or 0)):
            first.setdefault(veh, ts)
        print()
        print("%-22s %9s %9s  %s" % ("vehicle", "records", "differing", "first differing"))
        for veh in sorted(totals, key=str):
            print("%-22s %9d %9d  %s" % (veh, totals[veh], diffs.get(veh, 0),
                                         first.get(veh, "-")))

    if field_diffs:
        print()
        print("%-42s %8s  example" % ("field", "records"))
        for name, count in field_diffs.most_common():
            key, va, vb = examples[name]
            print("%-42s %8d  %s @ %s: %r vs %r" % (name, count, key[0], key[1], va, vb))

    print()
    if not only_a and not only_b and not field_diffs and not a_dupes and not b_dupes:
        print("DETERMINISTIC: both runs produced identical records.")
        return 0
    print("NOT DETERMINISTIC: see above. Missing/extra records point at the engine skipping")
    print("different records; differing fields point at the RNGs or at map iteration order.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
