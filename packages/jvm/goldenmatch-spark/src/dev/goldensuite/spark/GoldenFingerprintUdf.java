package dev.goldensuite.spark;

import org.apache.spark.sql.api.java.UDF1;

/** SQL surface for {@link NativeFingerprint}: JSON object in, 64 hex chars out.
 *
 * <p>Registered as {@code golden_fingerprint} and called on
 * {@code to_json(struct(...))}, so Spark builds the JSON in the engine and no
 * Python worker is involved at any point.
 *
 * <p>Thin on purpose, exactly like {@link GoldenScoreUdf}: everything with a
 * decision in it lives in Spark-free classes that are unit-testable without a
 * Spark classpath.
 */
public final class GoldenFingerprintUdf implements UDF1<String, String> {

  /** @param json a JSON object, typically from {@code to_json(struct(...))}
   *  @return 64 lowercase hex chars, or {@code null} if the input was null or
   *      could not be read as a JSON object */
  @Override
  public String call(String json) {
    return NativeFingerprint.of(json);
  }
}
