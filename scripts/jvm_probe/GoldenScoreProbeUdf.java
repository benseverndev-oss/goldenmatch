// Minimal Java UDF for the Spark Connect registration probe.
//
// It computes nothing interesting on purpose. The probe question is whether a
// Java UDF class, shipped to a Connect session via addArtifact, can be
// REGISTERED and CALLED from a Python Connect client -- not whether Rust is
// fast. Putting real scoring in here would confound a registration failure with
// a linkage failure.
//
// The signature is UDF2<String, String, Double> because that is the shape the
// real scorer needs (two string columns -> a double), so a probe that passes
// tells us the shape we actually want is registrable.
package dev.goldensuite.probe;

import org.apache.spark.sql.api.java.UDF2;

public class GoldenScoreProbeUdf implements UDF2<String, String, Double> {
  @Override
  public Double call(String a, String b) {
    if (a == null || b == null) {
      // Null policy stays with the caller (see score-cabi's header); returning
      // null here keeps this probe from asserting a policy it does not own.
      return null;
    }
    return a.equals(b) ? 1.0d : 0.0d;
  }
}
