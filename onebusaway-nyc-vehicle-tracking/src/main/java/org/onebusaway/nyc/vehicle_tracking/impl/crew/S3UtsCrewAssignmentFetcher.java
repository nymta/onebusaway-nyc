package org.onebusaway.nyc.vehicle_tracking.impl.crew;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.Date;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.amazonaws.auth.BasicAWSCredentials;
import com.amazonaws.services.s3.AmazonS3Client;
import com.amazonaws.services.s3.model.GetObjectRequest;
import com.amazonaws.services.s3.model.ObjectMetadata;
import com.amazonaws.services.s3.model.S3Object;

/**
 * Downloads the UTS CIS crew roster from S3. Credentials follow the usual AWS SDK chain
 * (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, or oba.crew.s3.* overrides).
 */
public class S3UtsCrewAssignmentFetcher {

  private static Logger _log = LoggerFactory.getLogger(S3UtsCrewAssignmentFetcher.class);

  private final String _bucket;
  private final String _key;
  private AmazonS3Client _s3;

  public S3UtsCrewAssignmentFetcher() {
    _bucket = System.getProperty("oba.crew.s3.bucket", "mtabuscis-uts-archive");
    _key = System.getProperty("oba.crew.s3.key", "latest/CIS.txt");
  }

  S3UtsCrewAssignmentFetcher(String bucket, String key, AmazonS3Client s3) {
    _bucket = bucket;
    _key = key;
    _s3 = s3;
  }

  public Date getLastModified() {
    try {
      ObjectMetadata meta = client().getObjectMetadata(_bucket, _key);
      return meta.getLastModified();
    } catch (Exception e) {
      _log.warn("Could not read S3 metadata for s3://{}/{}: {}", _bucket, _key, e.getMessage());
      return null;
    }
  }

  /**
   * @return local copy of the CIS file (may be reused if S3 object unchanged)
   */
  public File downloadIfChanged(Date knownLastModified, File target) throws IOException {
    Date remoteModified = getLastModified();
    if (knownLastModified != null && remoteModified != null
        && !remoteModified.after(knownLastModified) && target.exists()) {
      return target;
    }

    File parent = target.getParentFile();
    if (parent != null) {
      parent.mkdirs();
    }

    File temp = File.createTempFile("uts-cis-", ".csv", parent != null ? parent : null);
    try {
      _log.info("Downloading UTS crew roster from s3://{}/{}", _bucket, _key);
      S3Object object = client().getObject(new GetObjectRequest(_bucket, _key));
      Files.copy(object.getObjectContent(), temp.toPath(), StandardCopyOption.REPLACE_EXISTING);
      Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING);
      return target;
    } finally {
      temp.delete();
    }
  }

  private AmazonS3Client client() {
    if (_s3 != null) {
      return _s3;
    }
    String accessKey = System.getProperty("oba.crew.s3.accessKey", System.getenv("AWS_ACCESS_KEY_ID"));
    String secretKey = System.getProperty("oba.crew.s3.secretKey", System.getenv("AWS_SECRET_ACCESS_KEY"));
    if (accessKey != null && secretKey != null && accessKey.length() > 1) {
      _s3 = new AmazonS3Client(new BasicAWSCredentials(accessKey, secretKey));
    } else {
      _s3 = new AmazonS3Client();
    }
    return _s3;
  }
}
