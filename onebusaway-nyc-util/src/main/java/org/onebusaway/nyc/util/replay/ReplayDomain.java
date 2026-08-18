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
package org.onebusaway.nyc.util.replay;

/**
 * What a background task can affect, used to decide whether it may run during a replay.
 */
public enum ReplayDomain {

  /** Writes state the particle filter reads, so it changes inferred output. */
  INFERENCE_INPUT,

  /** Refreshes configuration, which can change behaviour anywhere mid-run. */
  CONFIG,

  /** Discovers or switches bundles. A switch resets per-vehicle state. */
  BUNDLE,

  /** Output plumbing, or fields written onto the published record but not read by inference. */
  OUTPUT,

  /** Neither read by inference nor published: metrics, APC, supplemental TDS data. */
  TELEMETRY;
}
