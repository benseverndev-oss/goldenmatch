package dev.goldensuite.spark;

import org.apache.spark.sql.api.java.UDF2;

/** SQL surface for {@link NativeSurvivorship}: a cluster's collected values plus
 * a strategy, one survivor out.
 *
 * <p>Registered as {@code golden_survivorship} and called on a
 * {@code collect_list} of the field. The first argument is {@code Object} for
 * the same reason {@link GoldenScoreUdf}'s is: Spark's Java type for an
 * {@code array<string>} column is version-dependent, and declaring
 * {@code java.util.List} throws {@code ClassCastException} against the Scala
 * {@code Seq} it actually passes. {@link SeqCoercion} handles it.
 */
public final class GoldenSurvivorshipUdf implements UDF2<Object, String, String> {

  @Override
  public String call(Object values, String strategy) {
    String[] vs = SeqCoercion.toStringArray(values);
    if (vs == null) {
      return null;
    }
    return NativeSurvivorship.merge(vs, strategy);
  }
}
