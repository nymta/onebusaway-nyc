#!/usr/bin/env python3
"""Connect to the RabbitMQ BusTech raw-GPS stream and verify the payload format.

Reads N messages and checks each is the RealtimeEnvelope -> CcLocationReport JSON
that OBA's inference input listener expects (InputServiceImpl.deserializeMessage).
Prints a field-coverage report + samples and captures raw bytes to samples.jsonl.

Usage (after `source mq_env.sh`):
    python3 verify_stream.py            # 10 messages from OBA_MQ_OFFSET (default 'last')
    python3 verify_stream.py -n 50 --offset last
    python3 verify_stream.py --quiet -n 200      # just the summary + capture
Read-only: it acks (required by streams) but never modifies OBA or the broker.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime

import mq_common

# CcLocationReport JSON keys OBA understands (TCIP superset; helium's model is a subset).
# Anything outside this set is "unknown" and risks a strict-Jackson parse failure on the OBA side.
KNOWN_CCR_KEYS = {
    "request-id", "vehicle", "status-info", "time-reported", "latitude", "longitude",
    "direction", "speed", "manufacturer-data", "operatorID", "runID", "destSignCode",
    "routeID", "localCcLocationReport", "data-quality", "emergencyCodes",
    # OBA TCIP-only extras (feed may omit; harmless if present):
    "languages", "trip", "last-timepoint", "onboard", "odometer-reading", "tripDistance", "blockID",
}
# Fields the OBA->NycRawLocationRecord mapping actually reads — must be present/usable.
REQUIRED_CCR_KEYS = [
    "request-id", "vehicle", "time-reported", "latitude", "longitude",
    "direction", "speed", "destSignCode", "localCcLocationReport",
]
# NYC-ish microdegree bounds (warn-only; MTABC/edge fixes can fall outside).
NYC_LAT = (40_000_000, 41_200_000)
NYC_LON = (-74_500_000, -73_400_000)


def parse_iso(s):
    """Tolerant ISO-8601 parse mirroring Joda's lenient dateTimeParser (e.g. '2026-01-12T02:08:19.0-00:00')."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        pass
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?([+-]\d{2}:?\d{2})?$", t)
    if not m:
        return None
    date, tm, frac, off = m.groups()
    frac = (frac or "0")[:6].ljust(6, "0")
    off = off or "+00:00"
    if re.match(r"^[+-]\d{4}$", off):
        off = off[:3] + ":" + off[3:]
    try:
        return datetime.fromisoformat("%sT%s.%s%s" % (date, tm, frac, off))
    except ValueError:
        return None


def validate(body):
    """Return (ok, errors, warnings, present_ccr_keys, unknown_ccr_keys, summary_dict)."""
    errors, warnings = [], []
    try:
        doc = json.loads(body)
    except Exception as e:
        return False, ["not valid JSON: %s" % e], [], set(), set(), {}

    if not isinstance(doc, dict) or "RealtimeEnvelope" not in doc:
        return False, ["missing top-level 'RealtimeEnvelope' object"], [], set(), set(), {}
    env = doc["RealtimeEnvelope"]
    for k, typ in (("UUID", str), ("timeReceived", (int, float)), ("CcLocationReport", dict)):
        if k not in env:
            errors.append("RealtimeEnvelope missing '%s'" % k)
        elif not isinstance(env[k], typ):
            errors.append("RealtimeEnvelope.%s wrong type" % k)
    ccr = env.get("CcLocationReport")
    if not isinstance(ccr, dict):
        return False, errors or ["CcLocationReport not an object"], warnings, set(), set(), {}

    present = set(ccr.keys())
    unknown = present - KNOWN_CCR_KEYS
    for k in REQUIRED_CCR_KEYS:
        if k not in ccr:
            errors.append("CcLocationReport missing required '%s'" % k)

    summary = {}
    # vehicle id
    veh = ccr.get("vehicle")
    if isinstance(veh, dict):
        for k in ("vehicle-id", "agency-id", "agencydesignator"):
            if k not in veh:
                errors.append("vehicle missing '%s'" % k)
        summary["vehicle"] = "%s_%s" % (veh.get("agencydesignator"), veh.get("vehicle-id"))
    else:
        errors.append("vehicle not an object")

    # lat/lon microdegrees
    lat, lon = ccr.get("latitude"), ccr.get("longitude")
    if isinstance(lat, int) and isinstance(lon, int):
        summary["lat"], summary["lon"] = lat / 1e6, lon / 1e6
        if not (NYC_LAT[0] <= lat <= NYC_LAT[1] and NYC_LON[0] <= lon <= NYC_LON[1]):
            warnings.append("lat/lon %.5f,%.5f outside NYC bounds" % (summary["lat"], summary["lon"]))
    else:
        errors.append("latitude/longitude not integer microdegrees")

    # time-reported
    tr = ccr.get("time-reported")
    if parse_iso(tr) is None:
        errors.append("time-reported not ISO-8601 parseable: %r" % tr)
    else:
        summary["time"] = tr

    # direction.deg
    direction = ccr.get("direction")
    if isinstance(direction, dict) and "deg" in direction:
        summary["bearing"] = direction.get("deg")
    else:
        warnings.append("direction.deg missing")

    summary["dsc"] = ccr.get("destSignCode")
    summary["speed"] = ccr.get("speed")
    rid = ccr.get("routeID")
    summary["route"] = rid.get("route-designator") if isinstance(rid, dict) else None
    # NMEA presence (full-fidelity field)
    local = ccr.get("localCcLocationReport") or {}
    nmea = (local.get("NMEA") or {}).get("sentence") if isinstance(local, dict) else None
    summary["nmea"] = bool(nmea) and any(s for s in nmea)

    return (len(errors) == 0), errors, warnings, present, unknown, summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-n", type=int, default=10, metavar="N", help="messages to read (default 10)")
    p.add_argument("--offset", default=None, help="x-stream-offset override (first|last|next|<int>|interval)")
    p.add_argument("--save", default="samples.jsonl", help="capture raw bodies here (default samples.jsonl; '' to skip)")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress per-message lines")
    args = p.parse_args()

    cfg = mq_common.load_config()
    if args.offset:
        cfg["offset"] = args.offset

    coverage = Counter()
    unknown_keys = Counter()
    received = valid = 0
    warned = ranged = timefail = 0
    first_samples = []
    sink = open(args.save, "w") if args.save else None

    def on_message(body):
        nonlocal received, valid, warned, ranged, timefail
        received += 1
        if sink:
            try:
                sink.write(json.dumps(json.loads(body), separators=(",", ":")) + "\n")
            except Exception:
                sink.write(body.decode("utf-8", "replace").replace("\n", " ") + "\n")
        ok, errors, warnings, present, unknown, s = validate(body)
        for k in present:
            coverage[k] += 1
        if s.get("nmea"):
            coverage["localCcLocationReport.NMEA(non-null)"] += 1
        for k in unknown:
            unknown_keys[k] += 1
        if ok:
            valid += 1
        if warnings:
            warned += 1
            if any("outside NYC" in w for w in warnings):
                ranged += 1
        if any("time-reported" in e for e in errors):
            timefail += 1
        if len(first_samples) < 2:
            first_samples.append(body)
        if not args.quiet:
            tag = "OK " if ok else "BAD"
            extra = ("  " + "; ".join(errors)) if errors else ("  ! " + "; ".join(warnings) if warnings else "")
            print("  #%-3d [%s] %-16s route=%-4s dsc=%-5s %s,%s bearing=%s nmea=%s%s" % (
                received, tag, s.get("vehicle", "?"), s.get("route"), s.get("dsc"),
                ("%.5f" % s["lat"]) if "lat" in s else "?", ("%.5f" % s["lon"]) if "lon" in s else "?",
                s.get("bearing"), s.get("nmea"), extra), flush=True)
        return received < args.n

    cfg_n = max(1, min(args.n, 200))
    try:
        conn = mq_common.open_connection(cfg)
    except Exception as e:
        sys.stderr.write("[mq] connection failed: %s\n" % e)
        sys.exit(1)
    ch = conn.channel()
    print("[mq] consuming %d message(s) from stream %r at offset %r ...\n"
          % (args.n, cfg["stream"], cfg["offset"]), flush=True)
    try:
        mq_common.consume_stream(ch, cfg["stream"], cfg["offset"], cfg_n, on_message)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        sys.stderr.write("[mq] consume error after %d msg(s): %s\n" % (received, e))
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if sink:
            sink.close()

    # ---- report ----
    print("\n================ verification summary ================")
    print("received: %d   schema-valid: %d   invalid: %d   with-warnings: %d" % (
        received, valid, received - valid, warned))
    print("time-reported parse failures: %d   out-of-NYC-bounds: %d" % (timefail, ranged))
    if args.save and received:
        print("captured raw payloads -> %s" % args.save)
    if received:
        print("\nfield coverage (of %d messages):" % received)
        for k in sorted(coverage, key=lambda x: (-coverage[x], x)):
            pct = 100.0 * coverage[k] / received
            flag = "" if k in KNOWN_CCR_KEYS or k.startswith("localCcLocationReport") else "  <-- UNKNOWN"
            print("    %-40s %4d  (%5.1f%%)%s" % (k, coverage[k], pct, flag))
    if unknown_keys:
        print("\n!! UNKNOWN CcLocationReport keys (not in OBA's TCIP model — may break strict parse):")
        for k, c in unknown_keys.most_common():
            print("    %-30s seen %d time(s)" % (k, c))
    else:
        print("\nno unknown CcLocationReport keys — payload matches the OBA RealtimeEnvelope contract.")
    if first_samples:
        print("\nsample payload:")
        try:
            print(json.dumps(json.loads(first_samples[0]), indent=2)[:2000])
        except Exception:
            print(first_samples[0][:2000])

    ok = received > 0 and valid == received and not unknown_keys
    print("\nRESULT: %s" % ("PASS — feed is in the expected format" if ok else "FAIL — see issues above"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
