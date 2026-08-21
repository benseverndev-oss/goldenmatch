package dev.goldensuite.spark;

import org.apache.spark.sql.api.java.UDF2;

/** SQL surface for {@link NativeTransform}: value + chain in, normalized value
 * out.
 *
 * <p>Registered as {@code golden_transform} and called as
 * {@code golden_transform(col, 'lowercase,strip')}. The chain is a second
 * COLUMN rather than baked into the class because {@code registerJavaFunction}
 * takes no constructor arguments -- one registration therefore serves every
 * chain in the query, exactly as the scorer's id argument serves every scorer.
 */
public final class GoldenTransformUdf implements UDF2<String, String, String> {

  /** @param value the value to normalize
   *  @param chain comma-separated transform names
   *  @return the normalized value, or {@code null} for a MISSING result */
  @Override
  public String call(String value, String chain) {
    return NativeTransform.apply(value, chain);
  }
}
