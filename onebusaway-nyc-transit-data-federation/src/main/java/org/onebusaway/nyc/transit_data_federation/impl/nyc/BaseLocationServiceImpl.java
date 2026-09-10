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
package org.onebusaway.nyc.transit_data_federation.impl.nyc;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import javax.annotation.PostConstruct;

import org.onebusaway.csv_entities.CsvEntityReader;
import org.onebusaway.csv_entities.ListEntityHandler;
import org.onebusaway.csv_entities.exceptions.CsvEntityIOException;
import org.onebusaway.container.refresh.Refreshable;
import org.onebusaway.geospatial.model.CoordinatePoint;
import org.onebusaway.gtfs.model.AgencyAndId;
import org.onebusaway.nyc.transit_data_federation.bundle.model.NycFederatedTransitDataBundle;
import org.onebusaway.nyc.transit_data_federation.bundle.tasks.stif.stifImport.NonRevenueStopData;
import org.onebusaway.nyc.transit_data_federation.impl.bundle.NycRefreshableResources;
import org.onebusaway.nyc.transit_data_federation.model.nyc.BaseLocationRecord;
import org.onebusaway.nyc.transit_data_federation.services.nyc.BaseLocationService;
import org.onebusaway.utility.ObjectSerializationLibrary;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.Envelope;
import org.locationtech.jts.geom.Geometry;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;

/**
 * Returns the name of a "base"/depot given an input coordinate.
 * 
 * Lookups are a linear scan over an immutable list rather than a JTS
 * {@code STRtree}: {@code STRtree.query()} unconditionally calls the
 * {@code synchronized} {@code build()}, so every read takes the tree's
 * monitor. This service is queried once per particle per GPS fix from the
 * inference pipeline, and under load that lock became the dominant blocker
 * (see BE-773). There are only a few dozen depots and terminals, so a scan
 * over pre-computed envelopes is cheap and involves no shared lock.
 * 
 * @author jmaki
 *
 */
@Component
class BaseLocationServiceImpl implements BaseLocationService {

  /**
   * One named polygon with its bounding box pre-computed, so a lookup can
   * reject most candidates without touching the geometry.
   */
  private static final class LocationEntry {

    private final Envelope _envelope;

    private final Geometry _geometry;

    private final String _baseName;

    LocationEntry(BaseLocationRecord record) {
      _geometry = record.getGeometry();
      _envelope = _geometry.getEnvelopeInternal();
      _baseName = record.getBaseName();
    }
  }

  private GeometryFactory _factory = new GeometryFactory();

  /**
   * Both lists are replaced wholesale on (re)load and never mutated, so
   * concurrent readers only ever see a complete, consistent snapshot.
   */
  private volatile List<LocationEntry> _baseLocations = Collections.emptyList();

  private volatile List<LocationEntry> _terminalLocations = Collections.emptyList();
  
  private Map<AgencyAndId, List<NonRevenueStopData>> _nonRevenueStopDataByTripId = new HashMap<AgencyAndId, List<NonRevenueStopData>>();

  private NycFederatedTransitDataBundle _bundle;

  @Autowired
  public void setBundle(NycFederatedTransitDataBundle bundle) {
    _bundle = bundle;
  }

  @PostConstruct
  @Refreshable(dependsOn = {NycRefreshableResources.TERMINAL_DATA, NycRefreshableResources.NON_REVENUE_STOP_DATA})
  public void setup() throws CsvEntityIOException, IOException, ClassNotFoundException {
    _baseLocations = readRecords(_bundle.getBaseLocationsPath());
    _terminalLocations = readRecords(_bundle.getTerminalLocationsPath());
    File nonRevenueStopsFile = _bundle.getNonRevenueStopsPath();
    if (nonRevenueStopsFile.exists())
      _nonRevenueStopDataByTripId = ObjectSerializationLibrary.readObject(nonRevenueStopsFile);
  }

  /****
   * {@link BaseLocationService} Interface
   ****/
  @Override
  public String getBaseNameForLocation(CoordinatePoint location) {
    return findNameForLocation(_baseLocations, location);
  }

  @Override
  public String getTerminalNameForLocation(CoordinatePoint location) {
    return findNameForLocation(_terminalLocations, location);
  }
  
  @Override
  public List<NonRevenueStopData> getNonRevenueStopsForTripId(AgencyAndId tripId) {
    // Returns null if there are not any non revenue stops for the trip.
    // Could return an empty list instead?
    return _nonRevenueStopDataByTripId.get(tripId);
  }

  /****
   * 
   ****/
  private List<LocationEntry> readRecords(File path) throws IOException,
      FileNotFoundException {

    if (!path.exists())
      return Collections.emptyList();

    CsvEntityReader reader = new CsvEntityReader();

    ListEntityHandler<BaseLocationRecord> records = new ListEntityHandler<BaseLocationRecord>();
    reader.addEntityHandler(records);

    try {
      reader.readEntities(BaseLocationRecord.class, new FileReader(path));
    } catch (CsvEntityIOException e) {
      throw new RuntimeException("Error parsing CSV file " + path, e);
    }

    List<BaseLocationRecord> values = records.getValues();

    List<LocationEntry> entries = new ArrayList<LocationEntry>(values.size());
    for (BaseLocationRecord record : values) {
      entries.add(new LocationEntry(record));
    }

    return Collections.unmodifiableList(entries);
  }

  private String findNameForLocation(List<LocationEntry> entries,
      CoordinatePoint location) {

    if (entries.isEmpty())
      return null;

    Coordinate coordinate = new Coordinate(location.getLon(), location.getLat());
    Point point = null;

    for (LocationEntry entry : entries) {
      if (!entry._envelope.intersects(coordinate))
        continue;

      if (point == null)
        point = _factory.createPoint(coordinate);

      if (entry._geometry.contains(point))
        return entry._baseName;
    }

    return null;
  }

}
