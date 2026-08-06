package org.onebusaway.nyc.transit_data_manager.adapters.api.processes;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.HashMap;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.joda.time.DateMidnight;
import org.onebusaway.nyc.transit_data_manager.adapters.ModelCounterpartConverter;
import org.onebusaway.nyc.transit_data_manager.adapters.data.OperatorAssignmentData;
import org.onebusaway.nyc.transit_data_manager.adapters.output.json.OperatorAssignmentFromTcip;
import org.onebusaway.nyc.transit_data_manager.adapters.output.model.json.OperatorAssignment;

import tcip_final_3_0_5_1.SCHOperatorAssignment;

/**
 * Parses a UTS CIS / crew CSV file into operator assignments keyed for inference lookup.
 * Uses the same adapter stack as TDM's /api/crew/{date}/list endpoint.
 */
public final class UtsCrewAssignmentParser {

  private static final Pattern OPERATOR_ID_PATTERN = Pattern.compile("^0*[a-zA-Z]*0*(\\d+)$");

  private UtsCrewAssignmentParser() {}

  public static HashMap<String, OperatorAssignment> loadForServiceDate(File cisFile,
      DateMidnight serviceDate) throws FileNotFoundException {

    UtsCrewAssignsToDataCreator process = new UtsCrewAssignsToDataCreator(cisFile);
    OperatorAssignmentData data = process.generateDataObject();

    List<SCHOperatorAssignment> raw = data.getOperatorAssignmentsByServiceDate(serviceDate);
    ModelCounterpartConverter<SCHOperatorAssignment, OperatorAssignment> converter =
        new OperatorAssignmentFromTcip();
    List<OperatorAssignment> deduped = new UTSUtil().listConvertOpAssignTcipToJson(converter, raw);

    HashMap<String, OperatorAssignment> output = new HashMap<String, OperatorAssignment>();
    for (OperatorAssignment oa : deduped) {
      if (oa.getPassId() == null || oa.getAgencyId() == null) {
        continue;
      }
      Matcher operatorIdMatcher = OPERATOR_ID_PATTERN.matcher(oa.getPassId());
      if (!operatorIdMatcher.matches()) {
        continue;
      }
      String tailoredId = operatorIdMatcher.group(1);
      output.put(oa.getAgencyId() + "_" + tailoredId, oa);
    }
    return output;
  }
}
