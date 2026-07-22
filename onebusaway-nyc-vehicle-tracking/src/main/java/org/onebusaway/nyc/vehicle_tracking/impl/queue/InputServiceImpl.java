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

package org.onebusaway.nyc.vehicle_tracking.impl.queue;

import java.util.ArrayList;
import java.util.concurrent.ConcurrentHashMap;

import javax.annotation.PostConstruct;

import org.joda.time.format.ISODateTimeFormat;

import org.apache.commons.lang.StringUtils;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.AnnotationIntrospector;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.module.jaxb.JaxbAnnotationIntrospector;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.queue.model.RealtimeEnvelope;
import org.onebusaway.nyc.transit_data_federation.services.tdm.VehicleAssignmentService;
import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.nyc.vehicle_tracking.services.inference.VehicleLocationInferenceService;
import org.onebusaway.nyc.vehicle_tracking.services.queue.InputService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;

import com.google.common.base.CharMatcher;

import tcip_final_3_0_5_1.CPTVehicleIden;
import tcip_final_3_0_5_1.CcLocationReport;

public abstract class InputServiceImpl {

	private static Logger _log = LoggerFactory
			.getLogger(InputServiceImpl.class);
	private String[] _depotPartitionKeys = null;
	private VehicleLocationInferenceService _vehicleLocationService;
	private VehicleAssignmentService _vehicleAssignmentService;
	private ConfigurationService _configurationService;
	private ObjectMapper _mapper;

	// Optional per-vehicle ingestion deadband (see passesDeadband). Default OFF -> prod unchanged.
	private final ConcurrentHashMap<String, long[]> _deadbandLastKept = new ConcurrentHashMap<String, long[]>();
	private boolean _deadbandEnabled = false;
	private double _deadbandMinMeters = 25.0;
	private long _deadbandMinIntervalMs = 10000L;
	private long _deadbandMaxAgeMs = 30000L;

	@Autowired
	public void setVehicleAssignmentService(
			VehicleAssignmentService vehicleAssignmentService) {
		_vehicleAssignmentService = vehicleAssignmentService;
	}

	@Autowired
	public void setConfigurationService(ConfigurationService configurationService) {
		_configurationService = configurationService;
	}

	@Autowired
	public void setVehicleLocationService(
			VehicleLocationInferenceService vehicleLocationService) {
		_vehicleLocationService = vehicleLocationService;
	}

	@SuppressWarnings("deprecation")
	@PostConstruct
	public void setup() {
		_mapper = new ObjectMapper();
		final AnnotationIntrospector jaxb = new JaxbAnnotationIntrospector();
		_mapper.setAnnotationIntrospector(jaxb);
		try {
			_deadbandEnabled = Boolean.getBoolean("oba.deadband.enabled");
			_deadbandMinMeters = Double.parseDouble(System.getProperty("oba.deadband.minMeters", "25"));
			_deadbandMinIntervalMs = 1000L * Long.parseLong(System.getProperty("oba.deadband.minIntervalSec", "10"));
			_deadbandMaxAgeMs = 1000L * Long.parseLong(System.getProperty("oba.deadband.maxAgeSec", "30"));
		} catch (Exception e) {
			_log.warn("deadband config parse error; using defaults", e);
		}
		if (_deadbandEnabled)
			_log.info("ingestion deadband ENABLED: minMeters=" + _deadbandMinMeters
					+ " minIntervalSec=" + (_deadbandMinIntervalMs / 1000)
					+ " maxAgeSec=" + (_deadbandMaxAgeMs / 1000));
	}

	public boolean processMessage(String address, byte[] buff) throws Exception {
		String contents = new String(buff);
		final RealtimeEnvelope message = deserializeMessage(contents);

		if (acceptMessage(message)) {
			_vehicleLocationService.handleRealtimeEnvelopeRecord(message);
			return true;
		}
		
		return false;
	}

	public RealtimeEnvelope deserializeMessage(String contents) {
		RealtimeEnvelope message = null;
		final String contentsPrintable = replaceNonPrintableCharacters(contents);
		final String contentsReplaced = replaceMessageContents(contentsPrintable);
		try {
			final JsonNode wrappedMessage = _mapper.readValue(contentsReplaced,
					JsonNode.class);
			final String ccLocationReportString = wrappedMessage.get(
					"RealtimeEnvelope").toString();
			message = _mapper.readValue(ccLocationReportString,
					RealtimeEnvelope.class);
		} catch (Exception e) {
			_log.warn("Received corrupted message from queue: ", e);
			_log.warn("Contents: " + contents);
			return null;
		}
		return message;
	}

	public String replaceNonPrintableCharacters(String contents) {
		return CharMatcher.javaIsoControl().removeFrom(contents);
	}

	public abstract String replaceMessageContents(String contents);
	
	
	public boolean acceptMessage(RealtimeEnvelope envelope) {
		if (envelope == null || envelope.getCcLocationReport() == null)
			return false;

		// local broker-less: when no TDM/depot-assignment data is available, accept every
		// well-formed envelope. Gated by inference-engine.acceptAllVehicles (default false ->
		// production behavior is unchanged: the depot-partition filtering below still applies).
		if (_configurationService != null
				&& Boolean.parseBoolean(_configurationService.getConfigurationValueAsString(
						"inference-engine.acceptAllVehicles", "false")))
			return passesDeadband(envelope);

		final CcLocationReport message = envelope.getCcLocationReport();
		final ArrayList<AgencyAndId> vehicleList = new ArrayList<AgencyAndId>();

		if (_depotPartitionKeys == null)
			return false;

		for (final String key : _depotPartitionKeys) {
			try {
				vehicleList.addAll(_vehicleAssignmentService
						.getAssignedVehicleIdsForDepot(key));
			} catch (final Exception e) {
				_log.warn("Error fetching assigned vehicles for depot " + key
						+ "; will retry.");
				continue;
			}
		}

		final CPTVehicleIden vehicleIdent = message.getVehicle();
		final AgencyAndId vehicleId = new AgencyAndId(
				vehicleIdent.getAgencydesignator(), vehicleIdent.getVehicleId()
						+ "");

		return vehicleList.contains(vehicleId) && passesDeadband(envelope);
	}

	/**
	 * Optional per-vehicle "send-on-delta" deadband: process a fix only if the vehicle moved
	 * >= minMeters since the last processed fix, subject to a minInterval rate cap and a maxAge
	 * staleness failsafe. Cuts particle-filter load by dropping redundant near-stationary pings
	 * while keeping the finer (5s) cadence when the bus is actually moving. Default OFF (returns
	 * true) so production behavior is unchanged; tune via -Doba.deadband.{enabled,minMeters,
	 * minIntervalSec,maxAgeSec}. Distance measured off the last KEPT fix so slow creep accumulates.
	 */
	protected boolean passesDeadband(RealtimeEnvelope envelope) {
		if (!_deadbandEnabled)
			return true;
		final CcLocationReport m = envelope.getCcLocationReport();
		final String key;
		final long now;
		final int lat, lon;
		try {
			key = m.getVehicle().getAgencydesignator() + "_" + m.getVehicle().getVehicleId();
			now = ISODateTimeFormat.dateTimeParser().parseDateTime(m.getTimeReported()).getMillis();
			lat = m.getLatitude();
			lon = m.getLongitude();
		} catch (Exception e) {
			return true; // malformed -> don't drop here; let the normal pipeline handle it
		}
		final long[] last = _deadbandLastKept.get(key);
		boolean keep;
		if (last == null) {
			keep = true;
		} else {
			final long age = now - last[2];
			if (age < 0L)
				keep = true; // out-of-order / clock reset
			else if (age < _deadbandMinIntervalMs)
				keep = false; // rate cap (bounds peak load even for fast movers)
			else if (age >= _deadbandMaxAgeMs)
				keep = true; // staleness failsafe (keep a stopped bus alive)
			else
				keep = distMeters((int) last[0], (int) last[1], lat, lon) >= _deadbandMinMeters;
		}
		if (keep)
			_deadbandLastKept.put(key, new long[] { lat, lon, now });
		return keep;
	}

	/** Great-circle distance in metres between two microdegree lat/lon points (haversine). */
	private static double distMeters(int latMicro1, int lonMicro1, int latMicro2, int lonMicro2) {
		final double lat1 = latMicro1 / 1e6, lon1 = lonMicro1 / 1e6;
		final double lat2 = latMicro2 / 1e6, lon2 = lonMicro2 / 1e6;
		final double dLat = Math.toRadians(lat2 - lat1), dLon = Math.toRadians(lon2 - lon1);
		final double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
				+ Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
				* Math.sin(dLon / 2) * Math.sin(dLon / 2);
		return 6371000.0 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
	}

	public String getDepotPartitionKey() {
		final StringBuilder sb = new StringBuilder();
		for (final String key : _depotPartitionKeys) {
			if (sb.length() > 0)
				sb.append(",");
			sb.append(key);
		}
		return sb.toString();
	}

	public void setDepotPartitionKey(String depotPartitionKey) {
		_log.info("depotPartitionKey=" + depotPartitionKey);
		if (depotPartitionKey != null && !depotPartitionKey.isEmpty())
			_depotPartitionKeys = depotPartitionKey.split(",");
		else
			_depotPartitionKeys = null;
	}

	public ObjectMapper getMapper() {
		return _mapper;
	}

	public void setMapper(ObjectMapper _mapper) {
		this._mapper = _mapper;
	}
}
