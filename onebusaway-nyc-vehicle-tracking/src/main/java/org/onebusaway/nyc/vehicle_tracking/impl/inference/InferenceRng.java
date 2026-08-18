/**
 * Copyright (C) 2011 Metropolitan Transportation Authority
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
package org.onebusaway.nyc.vehicle_tracking.impl.inference;

import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import org.onebusaway.gtfs.model.AgencyAndId;

import umontreal.iro.lecuyer.rng.MRG32k3a;
import umontreal.iro.lecuyer.rng.RandomStream;

/**
 * One independent random stream per vehicle, so inference output does not depend on thread timing.
 *
 * <p>The problem this solves: {@code CategoricalDist} and {@code ParticleFactoryImpl} each reached
 * their generator through a {@code static} field, so every vehicle drew from one shared stream. A
 * draw taken for vehicle A consumed a number vehicle B would otherwise have used, which makes the
 * sequence each vehicle sees a function of how the threads interleaved. Seeding that shared stream
 * does not help - a fixed starting point consumed in a varying order still varies.
 *
 * <p>Per vehicle rather than per thread on purpose. Striping already pins a vehicle to one thread,
 * so per-thread streams would be reproducible for a single process - but a vehicle lands on a
 * different stripe when the fleet is sharded differently, so per-thread seeding gives one answer for
 * a whole-fleet run and another for the same vehicle inside a shard. Keying on the vehicle makes the
 * result independent of how the work is divided.
 *
 * <p>Seeds are derived, not shared. Every vehicle could take the same seed and still be
 * reproducible, but then all vehicles walk the same sequence and their filters are correlated, which
 * changes the estimator's independence assumption. Mixing the global seed with the vehicle id costs
 * nothing and avoids that. The mix is the MurmurHash3 finalizer: raw or adjacent seeds produce
 * correlated early output from {@code java.util.Random}, and vehicle-id hashes are adjacent by
 * construction.
 *
 * <p>Usage is scoped, from {@code ProcessingTask.run()}:
 *
 * <pre>
 *   InferenceRng.enter(vehicleId);
 *   try { ... } finally { InferenceRng.exit(); }
 * </pre>
 *
 * <p>Outside such a scope the accessors fall back to a single shared bundle, so callers that are not
 * per-vehicle - the simulator, unit tests - keep working as before rather than throwing.
 */
public final class InferenceRng {

  /** Set with -Doba.inference.seed. Zero means "not reproducible", matching the old default. */
  private static final long DEFAULT_SEED = Long.getLong("oba.inference.seed", 0L);

  /**
   * MRG32k3a's six seed components are not free-form: the first three are taken mod m1 and the last
   * three mod m2, the first three must not be all zero and nor must the last three. Feeding it a
   * repeated raw hash - which is what the previous code did, {@code new long[] {seed x 6}} - risks a
   * degenerate stream. These bounds keep every component in range and non-zero.
   */
  private static final long M1 = 4294967087L;
  private static final long M2 = 4294944443L;

  private static volatile long _globalSeed = DEFAULT_SEED;

  private static final ConcurrentMap<AgencyAndId, Bundle> _byVehicle = new ConcurrentHashMap<>();

  private static final ThreadLocal<Bundle> _current = new ThreadLocal<>();

  /** Used when no vehicle scope is active. */
  private static volatile Bundle _fallback = new Bundle(DEFAULT_SEED);

  private InferenceRng() {
  }

  static final class Bundle {
    final Random categorical;
    final Random local;
    final RandomStream stream;

    Bundle(long seed) {
      if (seed == 0L) {
        // Preserve the historical meaning of seed 0: arbitrary, not reproducible.
        categorical = new Random();
        local = new Random();
        stream = new MRG32k3a();
      } else {
        categorical = new Random(mix(seed, 0x9E3779B97F4A7C15L));
        local = new Random(mix(seed, 0xC2B2AE3D27D4EB4FL));
        MRG32k3a s = new MRG32k3a();
        s.setSeed(streamSeed(seed));
        stream = s;
      }
    }
  }

  /** MurmurHash3 64-bit finalizer. Cheap, and decorrelates adjacent inputs. */
  private static long mix(long a, long b) {
    long z = a ^ b;
    z = (z ^ (z >>> 33)) * 0xFF51AFD7ED558CCDL;
    z = (z ^ (z >>> 33)) * 0xC4CEB9FE1A85EC53L;
    return z ^ (z >>> 33);
  }

  /** Six components in MRG32k3a's valid ranges, none zero. */
  private static long[] streamSeed(long seed) {
    long[] s = new long[6];
    for (int i = 0; i < 6; i++) {
      long m = (i < 3) ? M1 : M2;
      long v = mix(seed, 0x1000193L * (i + 1));
      s[i] = Math.floorMod(v, m - 1L) + 1L;   // 1 .. m-1, so never all zero
    }
    return s;
  }

  /**
   * Set the global seed and discard every existing stream, so a later run with the same seed starts
   * from the same place. Zero restores the non-reproducible default.
   */
  public static synchronized void setGlobalSeed(long seed) {
    _globalSeed = seed;
    _byVehicle.clear();
    _fallback = new Bundle(seed);
  }

  public static long getGlobalSeed() {
    return _globalSeed;
  }

  /** Bind this thread to a vehicle's streams for the duration of one record. */
  public static void enter(AgencyAndId vehicleId) {
    if (vehicleId == null) {
      _current.set(null);
      return;
    }
    final long seed = _globalSeed;
    _current.set(_byVehicle.computeIfAbsent(vehicleId,
        v -> new Bundle(seed == 0L ? 0L : mix(seed, v.toString().hashCode()))));
  }

  public static void exit() {
    _current.remove();
  }

  /** Drop a vehicle's streams, for when its inference instance is discarded. */
  public static void forget(AgencyAndId vehicleId) {
    if (vehicleId != null)
      _byVehicle.remove(vehicleId);
  }

  private static Bundle bundle() {
    final Bundle b = _current.get();
    return b != null ? b : _fallback;
  }

  public static Random categorical() {
    return bundle().categorical;
  }

  public static Random local() {
    return bundle().local;
  }

  public static RandomStream stream() {
    return bundle().stream;
  }
}
