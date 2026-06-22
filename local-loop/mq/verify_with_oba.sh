#!/usr/bin/env bash
# Strongest format proof: replay captured samples through OBA's *actual* deserializer.
# Mirrors InputServiceImpl.deserializeMessage (Jackson + JAXB introspector -> RealtimeEnvelope) and the
# InputQueueServiceImpl.replaceMessageContents key fixups, using the real onebusaway classes off the
# vehicle-tracking module classpath. Throwaway class is written to /tmp (nothing added to the repo).
#   ./verify_with_oba.sh [samples.jsonl]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/../env.sh"
SAMPLES="${1:-$HERE/samples.jsonl}"
[ -s "$SAMPLES" ] || { echo "no samples at $SAMPLES — run ./verify.sh first"; exit 2; }

MOD="$MAIN_REPO/onebusaway-nyc-vehicle-tracking"
[ -d "$MOD/target/classes" ] || { echo "build the module first: (cd $MAIN_REPO && mvn -q -pl onebusaway-nyc-vehicle-tracking -am -DskipTests install)"; exit 2; }

CPF=/tmp/oba-vt-cp.txt
echo "[oba-verify] resolving runtime classpath ..."
"$MVN" -q -f "$MAIN_REPO/pom.xml" -pl onebusaway-nyc-vehicle-tracking dependency:build-classpath \
  -Dmdep.outputFile="$CPF" -Dmdep.includeScope=runtime >/dev/null || { echo "classpath resolve failed"; exit 1; }
CP="$MOD/target/classes:$(cat "$CPF")"

mkdir -p /tmp/obaverify
cat > /tmp/obaverify/ObaVerify.java <<'JAVA'
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.module.jaxb.JaxbAnnotationIntrospector;
import com.google.common.base.CharMatcher;
import org.onebusaway.nyc.queue.model.RealtimeEnvelope;
import tcip_final_3_0_5_1.CcLocationReport;
import java.nio.file.*;
import java.util.*;

public class ObaVerify {
  public static void main(String[] args) throws Exception {
    ObjectMapper m = new ObjectMapper();
    m.setAnnotationIntrospector(new JaxbAnnotationIntrospector());
    int n = 0, ok = 0, bad = 0;
    for (String line : Files.readAllLines(Paths.get(args[0]))) {
      if (line.trim().isEmpty()) continue;
      n++;
      try {
        String c = CharMatcher.javaIsoControl().removeFrom(line)
            .replace("vehiclepowerstate", "vehiclePowerState")
            .replace("emergency-code", "emergencyCode");           // InputQueueServiceImpl fixups
        JsonNode w = m.readValue(c, JsonNode.class);
        String s = w.get("RealtimeEnvelope").toString();
        RealtimeEnvelope env = m.readValue(s, RealtimeEnvelope.class);
        CcLocationReport r = env.getCcLocationReport();
        if (r == null || r.getVehicle() == null) { bad++; System.out.println("  BAD line " + n + ": null ccr/vehicle"); continue; }
        // touch the exact fields VehicleLocationInferenceServiceImpl maps into NycRawLocationRecord:
        int la = r.getLatitude(); int lo = r.getLongitude();
        String veh = r.getVehicle().getAgencydesignator() + "_" + r.getVehicle().getVehicleId();
        String tr = r.getTimeReported();
        if (tr == null) { bad++; System.out.println("  BAD line " + n + ": null time-reported"); continue; }
        ok++;
      } catch (Exception e) {
        bad++;
        System.out.println("  BAD line " + n + ": " + e.getClass().getSimpleName() + " " + e.getMessage());
      }
    }
    System.out.println("OBA deserializer: parsed " + ok + "/" + n + " OK, " + bad + " failed");
    System.exit(bad == 0 && n > 0 ? 0 : 1);
  }
}
JAVA

echo "[oba-verify] compiling + running over $(wc -l < "$SAMPLES") sample(s) ..."
javac -cp "$CP" -d /tmp/obaverify /tmp/obaverify/ObaVerify.java || { echo "compile failed"; exit 1; }
java -cp "$CP:/tmp/obaverify" ObaVerify "$SAMPLES"
