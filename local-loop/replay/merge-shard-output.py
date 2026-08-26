#!/usr/bin/env python3
"""Merge N vehicle-sharded run-replay.sh processes' local output into one correctly-bucketed
S3 stream.

S3OutputQueueSenderServiceImpl rolls output parts by "whichever bucket happens to be open
when a record finishes computing," not by that record's own recordTimestamp - a record that
completes late lands in a later part than its true window. This script re-derives the true
bucket for every record and only writes to S3 once, so the fix and the merge are the same
step. Run one instance alongside two or more `run-replay.sh --shard i/n` invocations. A --shard
invocation never uploads its own (wrongly-bucketed) output - this is the only thing that does.

Usage:
  merge-shard-output.py --label TAG
  merge-shard-output.py --shard-dir DIR --shard-dir DIR [...] --s3-prefix s3://bucket/prefix/
                         [--roll-minutes 15] [--poll-seconds 15] [--state-file PATH]

--label is enough for the normal case: it's the same --tag you gave every `run-replay.sh --shard`
invocation, not their full generated OUT_DIR names (each shard reads its own clock for the
timestamp in the middle of that name, so two shards' full names never match each other - this
only relies on the tag prefix and the -shard-{i}-of-{n} suffix, which is also how it learns the
total shard count from the very first directory that appears, rather than guessing). Shard
directories default to everything under --data-dir starting with "{label}-" and ending in
"-shard-{i}-of-{n}", waited for if they don't exist yet - starting this before the shards is
the safer order. The S3 prefix defaults to
s3://ds-oba/replay/inference-outputs/{run_name}/, where run_name is shard 0's own full directory
name with the -shard-{i}-of-{n} suffix stripped off - i.e. label-wstart-vstart-to-vend, the same
shape an unsharded run's name would have. wstart specifically comes from whichever shard sorts
first; there's no single correct value across shards that each read their own clock, and it's
informational at this point, not a match key. --shard-dir/--s3-prefix override either default.

A bucket closes once every shard has produced at least one record whose own timestamp is a
full roll-window past that bucket's end - the measured straggler lag never exceeds one window
(confirmed: a nominally-"08:30" part held records back to 08:15:27). This is a fixed schedule,
not a live watermark read from JVM internals, which this process has no visibility into. The
tail buckets of a finished run are the exception: run-replay.sh writes a REPLAY_DONE sentinel
into its OUT_DIR on exit, and once every shard has one, this finalizes immediately rather than
waiting forever for a "next" nominal file that stopped replay will never produce.

Never exits non-zero from a steady-state error. A malformed line, a missing shard directory,
or a failed upload is logged and retried on the next tick - only startup/argument errors are
fatal, since once the shards run with REPLAY_OUT_S3=none this process is the only path to S3
for the whole run, and it needs to still be here at the end. Every record from a given source
part is written to its bucket as a full-file overwrite keyed on that source file's own name,
never an append - so a retry after a partial failure anywhere rewrites exactly the same bytes
instead of duplicating them, no matter where the previous attempt stopped.
"""
import argparse
import gzip
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

STATE_TMP_SUFFIX = ".tmp"
MAX_INGEST_ATTEMPTS = 5  # a source file failing this many times gets quarantined, not retried forever

_shutdown_requested = False


def _handle_signal(signum, _frame):
    global _shutdown_requested
    logging.warning("received signal %d; finishing this tick, then finalizing and exiting", signum)
    _shutdown_requested = True


def load_state(path):
    default = {"ingested": {}, "uploaded": [], "high_water": {}, "attempts": {}, "_roll_ms": None}
    if not os.path.exists(path):
        return default
    try:
        with open(path) as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("state file root is not an object")
        default.update(loaded)
        return default
    except Exception:
        logging.exception("state file %s is unreadable; starting fresh instead of crashing. "
                           "Already-uploaded buckets are safe (upload_bucket is idempotent by "
                           "S3 key), but a full re-scan of un-deleted shard files will follow.",
                           path)
        return default


def save_state(path, state):
    # Atomic: a crash mid-write leaves the old state file intact, never a truncated one.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=STATE_TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def completed_parts(shard_dir):
    """Non-.open files only - that suffix's absence is the engine's own "done rolling" signal.
    Any OSError (missing dir, permissions, a transient NFS-style hiccup) is treated the same
    way: nothing found this tick, try again next tick, don't let one bad shard directory stall
    ingestion for the other shard in the same tick."""
    try:
        names = os.listdir(shard_dir)
    except OSError as e:
        logging.warning("cannot list %s (%s); treating as empty this tick", shard_dir, e)
        return []
    return sorted(
        n for n in names
        if (n.endswith(".ndjson") or n.endswith(".ndjson.gz")) and not n.endswith(".open")
    )


def read_records(path):
    """Yields (recordTimestamp, raw_line). Tolerant: a bad line, or a timestamp that isn't
    actually a number, is logged and skipped - never fatal, and never silently mis-bucketed."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                logging.warning("unparseable line %d in %s; skipping", lineno, path)
                continue
            ts = rec.get("recordTimestamp")
            if not isinstance(ts, (int, float)):
                logging.warning("line %d in %s has no numeric recordTimestamp (%r); skipping",
                                 lineno, path, ts)
                continue
            yield int(ts), line


def bucket_dir(scratch_dir, bucket_start_ms):
    return os.path.join(scratch_dir, str(bucket_start_ms))


def fragment_name(shard_dir, source_name):
    # Deterministic and unique per (shard, source file) - writing it is always a full overwrite,
    # so re-ingesting the same source after a partial failure just rewrites the same bytes.
    safe_shard = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(shard_dir.rstrip("/")))
    return f"{safe_shard}__{source_name}.ndjson"


def part_name(bucket_start_ms):
    # Matches S3OutputQueueSenderServiceImpl's own PART_NAME_FORMAT: yyyyMMdd'T'HHmmss'ET'.
    import datetime
    import zoneinfo
    dt = datetime.datetime.fromtimestamp(bucket_start_ms / 1000, tz=zoneinfo.ZoneInfo("America/New_York"))
    return f"inferred-{dt.strftime('%Y%m%dT%H%M%S')}ET.ndjson.gz"


def ingest_one_file(shard_dir, name, scratch_dir, roll_ms):
    """Reads one source file and writes its records into per-bucket fragment files, each a full
    overwrite (not an append) so this whole function is safe to re-run from scratch on any
    prior partial failure. Returns the max recordTimestamp seen, or None if the file had no
    usable records at all."""
    path = os.path.join(shard_dir, name)
    by_bucket = {}
    max_ts = None
    for ts, line in read_records(path):
        bucket_start = (ts // roll_ms) * roll_ms
        by_bucket.setdefault(bucket_start, []).append(line)
        max_ts = ts if max_ts is None else max(max_ts, ts)
    frag_name = fragment_name(shard_dir, name)
    for bucket_start, lines in by_bucket.items():
        bdir = bucket_dir(scratch_dir, bucket_start)
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, frag_name), "w") as out:
            out.write("\n".join(lines) + "\n")
    return max_ts


def ingest_new_parts(shard_dirs, scratch_dir, state):
    roll_ms = state["_roll_ms"]
    for shard_dir in shard_dirs:
        ingested = state["ingested"].setdefault(shard_dir, [])
        ingested_set = set(ingested)
        attempts = state["attempts"].setdefault(shard_dir, {})
        for name in completed_parts(shard_dir):
            if name in ingested_set:
                continue
            path = os.path.join(shard_dir, name)
            try:
                max_ts = ingest_one_file(shard_dir, name, scratch_dir, roll_ms)
            except Exception:
                n = attempts[name] = attempts.get(name, 0) + 1
                if n >= MAX_INGEST_ATTEMPTS:
                    logging.exception("%s failed %d times; quarantining instead of retrying "
                                       "forever - inspect and fix manually", path, n)
                    quarantine_dir = os.path.join(shard_dir, ".quarantine")
                    try:
                        os.makedirs(quarantine_dir, exist_ok=True)
                        os.rename(path, os.path.join(quarantine_dir, name))
                    except OSError:
                        logging.exception("could not even move %s into %s; leaving in place, "
                                           "will keep failing to ingest it", path, quarantine_dir)
                    ingested.append(name)
                    ingested_set.add(name)
                else:
                    logging.exception("failed ingesting %s (attempt %d/%d); will retry next tick",
                                       path, n, MAX_INGEST_ATTEMPTS)
                continue
            if max_ts is not None:
                state["high_water"][shard_dir] = max(state["high_water"].get(shard_dir, max_ts), max_ts)
            try:
                os.remove(path)
            except OSError:
                logging.exception("ingested %s but could not delete it; harmless, it will just "
                                   "sit there since it's already marked ingested", path)
            ingested.append(name)
            ingested_set.add(name)
            attempts.pop(name, None)
            logging.info("ingested %s", path)


def safe_frontier(shard_dirs, state):
    """None until every shard has produced at least one record - nothing is safe to close
    based on partial information about a shard that hasn't started yet."""
    waters = [state["high_water"].get(d) for d in shard_dirs]
    if any(w is None for w in waters):
        return None
    return min(waters)


def upload_bucket(scratch_dir, bucket_start_ms, s3_prefix, state):
    bdir = bucket_dir(scratch_dir, bucket_start_ms)
    try:
        frag_names = sorted(os.listdir(bdir))
    except FileNotFoundError:
        return  # already uploaded and cleaned up, or genuinely never had anything
    if not frag_names:
        return
    name = part_name(bucket_start_ms)
    fd, tmp_gz = tempfile.mkstemp(dir=scratch_dir, suffix=".gz")
    os.close(fd)
    try:
        with gzip.open(tmp_gz, "wb") as dst:
            for frag in frag_names:
                with open(os.path.join(bdir, frag), "rb") as src:
                    dst.write(src.read())
        dest = s3_prefix.rstrip("/") + "/" + name
        subprocess.run(["aws", "s3", "cp", tmp_gz, dest, "--only-show-errors"],
                        check=True, timeout=300)
    except Exception:
        logging.exception("upload failed for bucket %d; leaving it open, will retry next tick",
                           bucket_start_ms)
        return
    finally:
        if os.path.exists(tmp_gz):
            os.remove(tmp_gz)
    for frag in frag_names:
        os.remove(os.path.join(bdir, frag))
    os.rmdir(bdir)
    state["uploaded"].append(bucket_start_ms)
    logging.info("uploaded %s (%d fragments)", dest, len(frag_names))


def close_ready_buckets(scratch_dir, shard_dirs, s3_prefix, state, finalize=False):
    roll_ms = state["_roll_ms"]
    frontier = safe_frontier(shard_dirs, state)
    uploaded = set(state["uploaded"])
    try:
        names = os.listdir(scratch_dir)
    except FileNotFoundError:
        names = []
    pending = set()
    for n in names:
        if not os.path.isdir(os.path.join(scratch_dir, n)):
            continue
        try:
            pending.add(int(n))
        except ValueError:
            logging.warning("unexpected entry %s in %s; ignoring it", n, scratch_dir)
    for bucket_start in sorted(pending - uploaded):
        if finalize or (frontier is not None and frontier >= bucket_start + 2 * roll_ms):
            upload_bucket(scratch_dir, bucket_start, s3_prefix, state)


DONE_SENTINEL = "REPLAY_DONE"


def all_shards_done(shard_dirs):
    """run-replay.sh writes this into its OUT_DIR on exit, success or failure - the only signal
    this process has that a shard is never going to produce another part. Without it, the tail
    buckets of a finished run wait forever: they can only close via the frontier rule, and that
    rule needs a "next" nominal file that will never arrive once replay has actually stopped."""
    return all(os.path.exists(os.path.join(d, DONE_SENTINEL)) for d in shard_dirs)


def run(shard_dirs, scratch_dir, s3_prefix, roll_minutes, poll_seconds, state_path):
    os.makedirs(scratch_dir, exist_ok=True)
    state = load_state(state_path)
    roll_ms = roll_minutes * 60_000
    if state["_roll_ms"] is not None and state["_roll_ms"] != roll_ms:
        raise SystemExit(
            f"--roll-minutes={roll_minutes} ({roll_ms}ms) doesn't match {state['_roll_ms']}ms "
            f"already recorded in {state_path} from an earlier run of this label - changing it "
            f"mid-run would misalign already-closed buckets against new ones. Pass the original "
            f"value, or delete the state file to intentionally start over.")
    state["_roll_ms"] = roll_ms

    while True:
        try:
            ingest_new_parts(shard_dirs, scratch_dir, state)
            close_ready_buckets(scratch_dir, shard_dirs, s3_prefix, state)
        except Exception:
            # ingest_new_parts/close_ready_buckets already catch their own per-file/per-bucket
            # errors - reaching here means something unexpected. Log it fully and keep going.
            logging.exception("unexpected error in merge tick; continuing")
        try:
            save_state(state_path, state)
        except Exception:
            logging.exception("failed writing state file; continuing with in-memory state")

        if _shutdown_requested or all_shards_done(shard_dirs):
            reason = "signal received" if _shutdown_requested else f"every shard wrote {DONE_SENTINEL}"
            logging.warning("finalizing (%s): uploading every remaining bucket regardless of frontier",
                             reason)
            try:
                ingest_new_parts(shard_dirs, scratch_dir, state)
                close_ready_buckets(scratch_dir, shard_dirs, s3_prefix, state, finalize=True)
                save_state(state_path, state)
            except Exception:
                logging.exception("error during finalize; some buckets may remain un-uploaded "
                                   "in %s - safe to rerun this script to pick them up", scratch_dir)
            return

        time.sleep(poll_seconds)


SHARD_DIR_RE = re.compile(r"-shard-(\d+)-of-(\d+)$")


def wait_for_shard_dirs(data_dir, label, timeout_s, poll_s):
    """Starting this before run-replay.sh has even created its OUT_DIR (which happens after
    crew-fetch, so there's a real delay) is the safer order - nothing can complete and get
    uploaded before this is watching for it. So --label mode waits for the directories rather
    than requiring them to exist already, up to a generous timeout in case the label's wrong.

    --label here is run-replay.sh's --tag, not its full generated OUT_DIR name - a shard's real
    directory is <tag>-<run timestamp>-<from>-to-<to>-shard-{i}-of-{n}, and the middle part is
    never the same across two independently-launched shard processes (each reads its own clock).
    So this only matches on the tag prefix and the -shard-{i}-of-{n} suffix - which is also how
    it learns the total shard count, from the very first directory that shows up, rather than
    guessing - and lets glob.glob() match anything in between."""
    import glob
    deadline = time.time() + timeout_s
    pattern = os.path.join(data_dir, f"{label}-*")
    logged_wait = False
    total = None
    while True:
        found = {}
        for d in glob.glob(pattern):
            if not os.path.isdir(d):
                continue
            m = SHARD_DIR_RE.search(d)
            if not m:
                continue
            i, n = int(m.group(1)), int(m.group(2))
            if total is None:
                total = n
            elif n != total:
                raise SystemExit(f"shard directories disagree on total shard count: {d} says "
                                  f"{n}, but another matching directory said {total}")
            found[i] = d
        if total is not None and len(found) >= total:
            missing = set(range(total)) - set(found)
            if missing:
                raise SystemExit(f"found >= {total} dirs matching {pattern} but indices "
                                  f"{sorted(missing)} are missing - check for a naming mismatch")
            return [found[i] for i in range(total)]
        if time.time() >= deadline:
            raise SystemExit(f"timed out after {timeout_s}s waiting for shard dirs matching "
                              f"{pattern} (found {sorted(found)} of {total})")
        if not logged_wait:
            logging.info("waiting for shard directories matching %s ...", pattern)
            logged_wait = True
        time.sleep(poll_s)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", default=None,
                   help="run label; enough on its own - see defaults above")
    p.add_argument("--shard-dir", action="append", default=None, dest="shard_dirs",
                   help="override: a shard's OUT_DIR; repeat once per shard")
    p.add_argument("--s3-prefix", default=None, help="override the default S3 destination")
    p.add_argument("--data-dir", default="/data/replay-out",
                   help="where run-replay.sh's OUT_DIR lives; only matters with --label")
    p.add_argument("--wait-timeout", type=int, default=1200,
                   help="with --label, give up waiting for shard dirs after this many seconds")
    p.add_argument("--roll-minutes", type=int, default=15)
    p.add_argument("--poll-seconds", type=int, default=15)
    p.add_argument("--state-file", default=None)
    p.add_argument("--scratch-dir", default=None)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                         format="%(asctime)s %(levelname)s %(message)s")

    if not args.shard_dirs:
        if not args.label:
            p.error("need --label, or --shard-dir given explicitly at least twice")
        args.shard_dirs = wait_for_shard_dirs(args.data_dir, args.label,
                                               args.wait_timeout, args.poll_seconds)
    elif len(args.shard_dirs) < 2:
        p.error("need at least 2 --shard-dir")

    # Shard 0's own full name (label-wstart-vstart-to-vend), not the bare --label - there's no
    # single correct wstart across shards (each read its own clock), so this just picks shard 0's.
    # Used to scope the S3 prefix, state file, and scratch dir all to *this* run: two different
    # runs replaying the same virtual-time window (e.g. re-running a failed attempt) produce the
    # same bucket_start_ms values, and a state/scratch path shared across runs would let one run's
    # "already uploaded" silently block the other's - confirmed live: a stale merge-state.json from
    # an earlier run at the same --data-dir left a later run's own bucket permanently stuck, marked
    # done for a bucket it had never actually uploaded itself.
    first_name = os.path.basename(args.shard_dirs[0].rstrip("/"))
    run_name = SHARD_DIR_RE.sub("", first_name)

    if not args.s3_prefix:
        if not args.label:
            p.error("need --label, or --s3-prefix given explicitly")
        args.s3_prefix = f"s3://ds-oba/replay/inference-outputs/{run_name}/"

    base = os.path.commonpath(args.shard_dirs)
    state_path = args.state_file or os.path.join(base, f"merge-state-{run_name}.json")
    scratch_dir = args.scratch_dir or os.path.join(base, f"merge-scratch-{run_name}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logging.info("merging %s -> %s (roll=%dmin, poll=%ds, state=%s)",
                 args.shard_dirs, args.s3_prefix, args.roll_minutes, args.poll_seconds, state_path)
    run(args.shard_dirs, scratch_dir, args.s3_prefix, args.roll_minutes, args.poll_seconds, state_path)


if __name__ == "__main__":
    main()
