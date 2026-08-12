// ARRAY-SHAPED probe UDF: one call scores MANY pairs.
//
// Why this shape at all. Spark Connect does not expose Catalyst, so the batch
// entry points (ColumnarBatch / ArrowColumnVector) are unreachable from a
// Connect client -- the same wall that rules out a custom Expression. The one
// registration mechanism Connect does allow, `registerJavaFunction`, is called
// PER ROW by Catalyst and hands you a UTF8String off an UnsafeRow. A per-row FFM
// downcall into Rust would be dominated by call overhead, which defeats the
// purpose of going native at all.
//
// Passing ARRAY columns amortises that: the caller groups pairs, and one UDF
// call covers the whole group. It does not achieve zero-copy -- that needs
// Arrow buffers this path never sees -- but it makes the downcall cost
// per-batch instead of per-row, which is the part that actually decides whether
// this is worth doing.
//
// Argument types are `Object` on purpose; see GoldenTypeProbeUdf. Spark's Java
// type for an ArrayType column is version-dependent, and a wrong declaration
// gives a ClassCastException that tells you nothing about feasibility.
package dev.goldensuite.probe;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.apache.spark.sql.api.java.UDF2;

public class GoldenScoreArrayProbeUdf implements UDF2<Object, Object, List<Double>> {

  /** Coerce whatever Spark passed for an ArrayType column into a List<String>.
   *
   * Handles java.util.List and Object[] directly. Anything else -- notably a
   * Scala Seq -- is read reflectively through `size()` / `apply(int)`, which
   * both Scala 2.12 and 2.13 collections expose. Reflection rather than a
   * compile-time Scala dependency keeps this probe from being pinned to one
   * Scala version, which would be its own confound.
   */
  private static List<String> coerce(Object o) {
    if (o == null) {
      return null;
    }
    if (o instanceof List) {
      List<?> raw = (List<?>) o;
      List<String> out = new ArrayList<>(raw.size());
      for (Object v : raw) {
        out.add(v == null ? null : v.toString());
      }
      return out;
    }
    if (o instanceof Object[]) {
      List<String> out = new ArrayList<>();
      for (Object v : Arrays.asList((Object[]) o)) {
        out.add(v == null ? null : v.toString());
      }
      return out;
    }
    try {
      Method size = o.getClass().getMethod("size");
      Method apply = o.getClass().getMethod("apply", int.class);
      int n = (Integer) size.invoke(o);
      List<String> out = new ArrayList<>(n);
      for (int i = 0; i < n; i++) {
        Object v = apply.invoke(o, i);
        out.add(v == null ? null : v.toString());
      }
      return out;
    } catch (ReflectiveOperationException e) {
      // Deliberately not swallowed into an empty list: an unreadable argument
      // must surface as a probe FAILURE, not as a plausible-looking result.
      throw new IllegalArgumentException(
          "unsupported array type from Spark: " + o.getClass().getName(), e);
    }
  }

  @Override
  public List<Double> call(Object a, Object b) {
    List<String> as = coerce(a);
    List<String> bs = coerce(b);
    if (as == null || bs == null) {
      return null;
    }
    if (as.size() != bs.size()) {
      throw new IllegalArgumentException(
          "array length mismatch: " + as.size() + " vs " + bs.size());
    }
    // Trivial on purpose -- the question is the CALLING CONVENTION, not the
    // kernel. Real scoring goes through score-cabi once this shape is proven.
    List<Double> out = new ArrayList<>(as.size());
    for (int i = 0; i < as.size(); i++) {
      String x = as.get(i);
      String y = bs.get(i);
      // Null policy stays with the caller (see score-cabi's header): report
      // null rather than inventing a similarity for a missing value.
      out.add(x == null || y == null ? null : (x.equals(y) ? 1.0d : 0.0d));
    }
    return out;
  }
}
