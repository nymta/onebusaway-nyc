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
package org.onebusaway.nyc.vehicle_tracking.impl.queue;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.StringWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.GZIPOutputStream;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;

import org.onebusaway.nyc.transit_data.model.NycQueuedInferredLocationBean;
import org.onebusaway.nyc.vehicle_tracking.services.queue.OutputQueueSenderService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.databind.MappingJsonFactory;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Writes inference output to rolling NDJSON files instead of publishing on ZMQ, so a replay's output
 * is a durable artifact rather than a live stream nobody may be subscribed to. Each record is
 * serialized exactly as {@link OutputQueueSenderServiceImpl#enqueue} serializes it, so downstream
 * tooling reads both formats identically.
 *
 * <p>A part is written as {@code <name>.open} and renamed on completion, so an external uploader can
 * take any file without the {@code .open} suffix. The upload itself lives in the replay scripts
 * ({@code aws s3 mv}), not here: the AWS SDK on this classpath is 1.3.9 (2012), which signs with
 * SigV2 and gets AccessDenied from any modern bucket.
 *
 * <p>Parts roll on virtual time, not record count: each part covers one {@code rollMinutes} window
 * of each record's own {@code recordTimestamp}, and the filename names that window
 * ({@code inferred-<windowStartUtc>.ndjson[.gz]}), so which part to resume from after a crash is
 * legible from the directory listing alone. Records are not strictly time-ordered across the 46
 * stripes, so a rare straggler from an already-closed window lands in the currently open part
 * instead of reopening a finished one; the part name is which window it was collected *for*, not a
 * strict guarantee every record in it falls inside that window.
 *
 * <p>Select with {@code -Die.output.queue=S3OutputQueueSenderServiceImpl}. Properties:
 *
 * <ul>
 * <li>{@code oba.replay.output.dir} - spool directory (default /tmp/oba-replay-out)
 * <li>{@code oba.replay.output.rollMinutes} - virtual minutes per part (default 15)
 * <li>{@code oba.replay.output.gzip} - default true; set false for plain NDJSON that
 *     compare-replay-runs.py can read directly
 * </ul>
 *
 * <p>Closing happens in a JVM shutdown hook rather than only in {@code @PreDestroy}, because
 * {@code -Dreplay.exitWhenDone} ends the run with {@code System.exit}, which does not run the Spring
 * lifecycle.
 */
public class S3OutputQueueSenderServiceImpl implements OutputQueueSenderService {

  private static final Logger _log = LoggerFactory.getLogger(S3OutputQueueSenderServiceImpl.class);

  private static final int LOG_EVERY = 50000;

  private final ObjectMapper _mapper = new ObjectMapper();

  private boolean _isPrimaryInferenceInstance = true;
  private String _primaryHostname = null;

  private static final java.time.format.DateTimeFormatter PART_NAME_FORMAT =
      java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'")
          .withZone(java.time.ZoneOffset.UTC);

  private File _dir;
  private long _rollMillis;
  private boolean _gzip;

  private final Object _lock = new Object();
  private Writer _writer;
  private File _currentPart;      // the .open file being written
  private File _finalPart;        // its name once complete
  private long _currentBucketStart = Long.MIN_VALUE; // start of the window the open part covers
  private long _linesInPart = 0;
  private long _totalLines = 0;
  private boolean _closed = false;
  private final List<String> _partNames = new ArrayList<String>();

  @PostConstruct
  public void setup() throws IOException {
    _dir = new File(System.getProperty("oba.replay.output.dir", "/tmp/oba-replay-out"));
    _rollMillis = Integer.getInteger("oba.replay.output.rollMinutes", 15) * 60_000L;
    _gzip = !"false".equalsIgnoreCase(System.getProperty("oba.replay.output.gzip", "true"));

    if (!_dir.isDirectory() && !_dir.mkdirs())
      throw new IOException("cannot create output spool dir " + _dir);

    // The first part opens lazily on the first record, once its recordTimestamp fixes the window.
    _log.warn("inference output -> {} (roll every {} virtual minutes)", _dir, _rollMillis / 60_000L);

    Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() {
      @Override
      public void run() {
        close();
      }
    }, "s3-output-close"));
  }

  @Override
  public void enqueue(NycQueuedInferredLocationBean r) {
    if (!_isPrimaryInferenceInstance)
      return;
    try {
      final StringWriter sw = new StringWriter();
      final MappingJsonFactory jsonFactory = new MappingJsonFactory();
      final JsonGenerator jsonGenerator = jsonFactory.createJsonGenerator(sw);
      _mapper.writeValue(jsonGenerator, r);
      sw.close();

      final Long ts = r.getRecordTimestamp();

      synchronized (_lock) {
        if (_closed)
          return;
        if (_currentBucketStart == Long.MIN_VALUE) {
          openNextPart(bucketStartFor(ts));
        } else if (ts != null && bucketStartFor(ts) > _currentBucketStart) {
          rollPart(bucketStartFor(ts));
        }
        _writer.write(sw.toString());
        _writer.write('\n');
        _linesInPart++;
        _totalLines++;
        if (_totalLines % LOG_EVERY == 0)
          _log.warn("inference output: {} records written", _totalLines);
      }
    } catch (final IOException e) {
      _log.error("could not write inferred location record: " + e.getMessage(), e);
    }
  }

  /** Window start containing ts, or the current window if ts is unavailable (should not happen). */
  private long bucketStartFor(Long ts) {
    if (ts == null)
      return _currentBucketStart == Long.MIN_VALUE ? 0L : _currentBucketStart;
    return ts - Math.floorMod(ts, _rollMillis);
  }

  private void openNextPart(long bucketStart) throws IOException {
    _currentBucketStart = bucketStart;
    String name = String.format("inferred-%s.ndjson%s",
        PART_NAME_FORMAT.format(java.time.Instant.ofEpochMilli(bucketStart)), _gzip ? ".gz" : "");
    _finalPart = new File(_dir, name);
    _currentPart = new File(_dir, name + ".open");
    FileOutputStream fos = new FileOutputStream(_currentPart);
    _writer = new BufferedWriter(new OutputStreamWriter(
        _gzip ? new GZIPOutputStream(fos) : fos, StandardCharsets.UTF_8));
    _linesInPart = 0;
  }

  private void rollPart(long newBucketStart) throws IOException {
    _writer.close();
    finishPart();
    openNextPart(newBucketStart);
  }

  /** Rename marks the part complete; the replay script uploads anything without the .open suffix. */
  private void finishPart() {
    if (_currentPart.renameTo(_finalPart)) {
      _partNames.add(_finalPart.getName());
      _log.warn("part complete: {} ({} bytes)", _finalPart.getName(), _finalPart.length());
    } else {
      _log.error("could not rename {} to {}; part will not be uploaded", _currentPart, _finalPart);
    }
  }

  void close() {
    synchronized (_lock) {
      if (_closed)
        return;
      _closed = true;
      try {
        if (_writer != null) {
          _writer.close();
          if (_linesInPart > 0)
            finishPart();
          else
            _currentPart.delete();
        }
      } catch (IOException e) {
        _log.error("closing output failed: " + e.getMessage(), e);
      }
      _log.warn("inference output closed: {} records in {} part(s)", _totalLines,
          _partNames.size());
    }
  }

  @PreDestroy
  public void destroy() {
    close();
  }

  @Override
  public void setIsPrimaryInferenceInstance(boolean isPrimary) {
    _isPrimaryInferenceInstance = isPrimary;
  }

  @Override
  public boolean getIsPrimaryInferenceInstance() {
    return _isPrimaryInferenceInstance;
  }

  @Override
  public void setPrimaryHostname(String hostname) {
    _primaryHostname = hostname;
  }

  @Override
  public String getPrimaryHostname() {
    return _primaryHostname;
  }
}
