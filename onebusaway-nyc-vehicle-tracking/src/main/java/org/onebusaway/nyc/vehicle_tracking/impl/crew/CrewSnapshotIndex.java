/**
 * Copyright (C) 2026 Metropolitan Transportation Authority
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *         http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.onebusaway.nyc.vehicle_tracking.impl.crew;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.NavigableMap;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Picks the UTS snapshot that was current at a given instant, from a directory of files prefetched
 * out of the archive bucket.
 *
 * <p>Selection is as-of: the newest snapshot generated at or before the instant asked for. Choosing by
 * date prefix instead would hand the engine a roster that did not exist yet, because the archive
 * publishes the next service date the evening before.
 */
class CrewSnapshotIndex {

  private static final Logger _log = LoggerFactory.getLogger(CrewSnapshotIndex.class);

  /** crew_YYYYMMDD_HHMMSS.csv, the archive's own naming. The timestamp is UTC. */
  private static final Pattern SNAPSHOT = Pattern.compile("crew_(\\d{8})_(\\d{6})\\.csv$");

  private final NavigableMap<Long, File> _byInstant = new TreeMap<Long, File>();

  CrewSnapshotIndex(File root) throws FileNotFoundException {
    if (root == null || !root.isDirectory()) {
      throw new FileNotFoundException("crew snapshot directory does not exist: " + root);
    }
    index(root);
    if (_byInstant.isEmpty()) {
      throw new FileNotFoundException("no crew_YYYYMMDD_HHMMSS.csv files under " + root);
    }
    _log.info("Indexed {} UTS snapshots from {} covering {} .. {}", _byInstant.size(), root,
        _byInstant.firstKey(), _byInstant.lastKey());
  }

  private void index(File dir) {
    File[] entries = dir.listFiles();
    if (entries == null) {
      return;
    }
    // Accepts both a flat directory and one date-prefixed subdirectory per day, so the prefetch can
    // mirror the bucket layout or flatten it.
    List<File> sorted = new ArrayList<File>();
    Collections.addAll(sorted, entries);
    Collections.sort(sorted);
    for (File entry : sorted) {
      if (entry.isDirectory()) {
        index(entry);
        continue;
      }
      Long instant = instantOf(entry.getName());
      if (instant == null) {
        continue;
      }
      File previous = _byInstant.put(instant, entry);
      if (previous != null && !previous.equals(entry)) {
        _log.warn("Two snapshots claim the same instant: {} and {}; keeping {}",
            previous.getName(), entry.getName(), entry.getName());
      }
    }
  }

  /** Epoch millis encoded in the filename, or null if the name is not a snapshot. */
  static Long instantOf(String filename) {
    Matcher m = SNAPSHOT.matcher(filename);
    if (!m.find()) {
      return null;
    }
    String digits = m.group(1) + m.group(2);
    try {
      return java.time.LocalDateTime
          .parse(digits, java.time.format.DateTimeFormatter.ofPattern("yyyyMMddHHmmss"))
          .toInstant(java.time.ZoneOffset.UTC).toEpochMilli();
    } catch (Exception e) {
      _log.warn("Unparseable snapshot name {}", filename);
      return null;
    }
  }

  /**
   * @return newest snapshot generated at or before epochMillis, or null if the index starts later
   */
  File asOf(long epochMillis) {
    java.util.Map.Entry<Long, File> entry = _byInstant.floorEntry(epochMillis);
    return entry == null ? null : entry.getValue();
  }

  long firstInstant() {
    return _byInstant.firstKey();
  }

  int size() {
    return _byInstant.size();
  }
}
