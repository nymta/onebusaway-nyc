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
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
import javax.net.ssl.SSLContext;
import javax.servlet.ServletContext;

import org.onebusaway.nyc.util.configuration.ConfigurationService;
import org.onebusaway.nyc.vehicle_tracking.services.queue.InputService;
import org.onebusaway.nyc.vehicle_tracking.services.queue.InputTask;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.web.context.ServletContextAware;

import com.rabbitmq.client.Address;
import com.rabbitmq.client.Channel;
import com.rabbitmq.client.Connection;
import com.rabbitmq.client.ConnectionFactory;
import com.rabbitmq.client.DeliverCallback;

/**
 * Consume the BusTech / Cambridge Systematics raw-GPS feed directly from a RabbitMQ <b>stream</b>
 * (over AMQP 0.9.1) and inject each message into the inference engine.
 * <p>
 * Drop-in alternative to {@link PartitionedInputQueueListenerTask} (which reads ZeroMQ). The message
 * body on the wire is the same {@code {"RealtimeEnvelope": {...}}} JSON, so this reuses the exact same
 * {@link InputService#processMessage(String, byte[])} pipeline (deserialize -> acceptMessage ->
 * handleRealtimeEnvelopeRecord). Production replacement for the {@code local-loop/mq} Python bridge:
 * no external process and no ZeroMQ hop.
 * <p>
 * Select it by building the webapp with {@code ie.listener=RabbitMqInputQueueListenerTask} and seeding
 * these ConfigurationService keys (e.g. via a TDM or local data-sources.xml MethodInvokingFactoryBeans):
 * <pre>
 *   inference-engine.rabbitmq.addresses     host1:5671,host2:5671   (required; comma-separated)
 *   inference-engine.rabbitmq.username      ...                     (required)
 *   inference-engine.rabbitmq.password      ...                     (required)
 *   inference-engine.rabbitmq.streamName    ...                     (required; the stream/queue name)
 *   inference-engine.rabbitmq.virtualHost   /                       (default "/")
 *   inference-engine.rabbitmq.ssl           true                    (default true; AMQPS on 5671)
 *   inference-engine.rabbitmq.sslInsecure   false                   (default false; true skips cert/host checks)
 *   inference-engine.rabbitmq.offset        last                    (x-stream-offset: first|last|next|&lt;int&gt;|interval)
 *   inference-engine.rabbitmq.prefetch      100                     (basic.qos; required &gt; 0 for streams)
 * </pre>
 * Depot-partition filtering still applies in {@code acceptMessage}; set
 * {@code inference-engine.acceptAllVehicles=true} in environments without a TDM.
 */
public class RabbitMqInputQueueListenerTask implements InputTask, ServletContextAware {

  private static final Logger _log = LoggerFactory.getLogger(RabbitMqInputQueueListenerTask.class);

  private InputService _inputService;
  private ConfigurationService _configurationService;
  private String _depotPartitionKey;

  private ExecutorService _connectExecutor;
  private volatile Connection _connection;
  private volatile Channel _channel;
  private volatile boolean _shutdown = false;

  @Autowired
  @Qualifier("queueInputService")
  public void setInputService(InputService inputService) {
    _inputService = inputService;
  }

  @Autowired
  public void setConfigurationService(ConfigurationService configurationService) {
    _configurationService = configurationService;
  }

  @Override
  public void setServletContext(ServletContext servletContext) {
    if (servletContext != null) {
      setDepotPartitionKey(servletContext.getInitParameter("depot.partition.key"));
      _log.info("servlet context provided depot.partition.key=" + _depotPartitionKey);
    }
  }

  public void setDepotPartitionKey(String depotPartitionKey) {
    _depotPartitionKey = depotPartitionKey;
  }

  @Override
  public String getDepotPartitionKey() {
    return _depotPartitionKey;
  }

  private String cfg(String key, String dflt) {
    return _configurationService == null ? dflt
        : _configurationService.getConfigurationValueAsString(key, dflt);
  }

  @PostConstruct
  public void setup() {
    _inputService.setDepotPartitionKey(_depotPartitionKey);

    final String addresses = cfg("inference-engine.rabbitmq.addresses", null);
    final String username = cfg("inference-engine.rabbitmq.username", null);
    final String streamName = cfg("inference-engine.rabbitmq.streamName", null);
    if (addresses == null || username == null || streamName == null) {
      _log.info("RabbitMQ input queue is not attached; inference-engine.rabbitmq.{addresses,username,streamName} not all set.");
      return;
    }

    _connectExecutor = Executors.newSingleThreadExecutor();
    _connectExecutor.execute(new ConnectTask(addresses, username, streamName));
  }

  @PreDestroy
  public void destroy() {
    _shutdown = true;
    if (_connectExecutor != null)
      _connectExecutor.shutdownNow();
    try {
      if (_channel != null && _channel.isOpen())
        _channel.close();
    } catch (Exception e) {
      _log.debug("error closing channel", e);
    }
    try {
      if (_connection != null && _connection.isOpen())
        _connection.close();
    } catch (Exception e) {
      _log.debug("error closing connection", e);
    }
  }

  private List<Address> parseAddresses(String csv, int defaultPort) {
    final List<Address> out = new ArrayList<Address>();
    for (final String tok : csv.split(",")) {
      final String t = tok.trim();
      if (t.isEmpty())
        continue;
      final int c = t.lastIndexOf(':');
      if (c > 0)
        out.add(new Address(t.substring(0, c), Integer.parseInt(t.substring(c + 1))));
      else
        out.add(new Address(t, defaultPort));
    }
    return out;
  }

  /** x-stream-offset: a plain integer is a stream offset; otherwise a string (first|last|next|interval). */
  private Object offsetValue(String offset) {
    try {
      return Long.valueOf(offset);
    } catch (NumberFormatException e) {
      return offset;
    }
  }

  private class ConnectTask implements Runnable {
    private final String addresses;
    private final String username;
    private final String streamName;

    ConnectTask(String addresses, String username, String streamName) {
      this.addresses = addresses;
      this.username = username;
      this.streamName = streamName;
    }

    @Override
    public void run() {
      final boolean ssl = Boolean.parseBoolean(cfg("inference-engine.rabbitmq.ssl", "true"));
      final boolean sslInsecure = Boolean.parseBoolean(cfg("inference-engine.rabbitmq.sslInsecure", "false"));
      final int defaultPort = ssl ? 5671 : 5672;
      final int prefetch = _configurationService == null ? 100
          : _configurationService.getConfigurationValueAsInteger("inference-engine.rabbitmq.prefetch", 100);
      final String offset = cfg("inference-engine.rabbitmq.offset", "last");

      long backoff = 2000L;
      while (!_shutdown) {
        try {
          final ConnectionFactory factory = new ConnectionFactory();
          factory.setUsername(username);
          factory.setPassword(cfg("inference-engine.rabbitmq.password", ""));
          factory.setVirtualHost(cfg("inference-engine.rabbitmq.virtualHost", "/"));
          factory.setAutomaticRecoveryEnabled(true);
          factory.setTopologyRecoveryEnabled(true);
          factory.setNetworkRecoveryInterval(5000);
          if (ssl) {
            if (sslInsecure) {
              factory.useSslProtocol();                       // trust-all (dev/self-signed only)
            } else {
              factory.useSslProtocol(SSLContext.getDefault()); // validate against the JVM trust store
              factory.enableHostnameVerification();
            }
          }

          final List<Address> addrs = parseAddresses(addresses, defaultPort);
          _log.warn("RabbitMQ input: connecting to " + addrs + " stream=" + streamName
              + " offset=" + offset + " ssl=" + ssl);
          _connection = factory.newConnection(addrs);
          _channel = _connection.createChannel();
          _channel.basicQos(Math.max(1, prefetch));            // streams require a positive prefetch

          final Map<String, Object> args = new HashMap<String, Object>();
          args.put("x-stream-offset", offsetValue(offset));

          final Channel ch = _channel;
          final DeliverCallback deliver = (consumerTag, delivery) -> {
            try {
              _inputService.processMessage(streamName, delivery.getBody());
            } catch (Exception e) {
              _log.warn("RabbitMQ input: processMessage failed; skipping message", e);
            } finally {
              try {
                ch.basicAck(delivery.getEnvelope().getDeliveryTag(), false);
              } catch (Exception e) {
                _log.debug("ack failed", e);
              }
            }
          };

          _channel.basicConsume(streamName, false, args, deliver, consumerTag -> {});
          _log.warn("RabbitMQ input: consuming stream " + streamName);
          return; // connected; auto-recovery handles subsequent drops
        } catch (Exception e) {
          if (_shutdown)
            return;
          _log.error("RabbitMQ input: connect failed (" + e.getMessage() + "); retrying in " + (backoff / 1000) + "s");
          try {
            Thread.sleep(backoff);
          } catch (InterruptedException ie) {
            return;
          }
          backoff = Math.min(backoff * 2, 30000L);
        }
      }
    }
  }
}
