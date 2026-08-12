package dev.goldensuite.spark;

import java.lang.reflect.Method;
import java.util.List;

/** Turns whatever Spark passes for an {@code array<string>} column into a
 * {@code String[]}.
 *
 * <h2>Measured, not guessed</h2>
 *
 * Probe run 31611464914 (pyspark 4.2.0) reports the runtime type as
 * {@code scala.collection.immutable.ArraySeq$ofRef} -- a <b>Scala</b> Seq, not a
 * {@code java.util.List}. Declaring {@code java.util.List} is the obvious guess
 * and what Spark's own Java UDF examples suggest; it throws
 * {@code ClassCastException}, which reads as "the array shape does not work"
 * when the shape is fine and only the declaration was wrong.
 *
 * <p>So the UDF takes {@code Object} and coercion happens here, covering the
 * types Spark plausibly passes across versions. Reflection rather than a
 * compile-time Scala dependency keeps the jar from being pinned to one Scala
 * version -- Spark's Scala version is not ours to assume.
 *
 * <p>Spark-free on purpose: this is the piece most likely to be wrong, and it is
 * unit-testable with no Spark on the classpath.
 */
public final class SeqCoercion {

  private SeqCoercion() {}

  /** @return the values as a {@code String[]}, or {@code null} if {@code o} is null
   *  @throws IllegalArgumentException if the type cannot be read -- an
   *      unreadable argument must surface as a failure, never as an empty batch
   *      that scores nothing and looks like a clean run */
  public static String[] toStringArray(Object o) {
    if (o == null) {
      return null;
    }
    if (o instanceof String[]) {
      return (String[]) o;
    }
    if (o instanceof Object[]) {
      return box((Object[]) o);
    }
    if (o instanceof List) {
      List<?> raw = (List<?>) o;
      String[] out = new String[raw.size()];
      for (int i = 0; i < out.length; i++) {
        Object v = raw.get(i);
        out[i] = (v == null) ? null : v.toString();
      }
      return out;
    }
    // Scala Seq (the observed case). `ArraySeq$ofRef` wraps an Object[], so
    // prefer reaching the backing array over iterating the collection.
    try {
      Method array = findNoArg(o.getClass(), "array");
      if (array != null) {
        Object backing = array.invoke(o);
        if (backing instanceof Object[]) {
          return box((Object[]) backing);
        }
      }
    } catch (ReflectiveOperationException ignored) {
      // Fall through to size()/apply(int), which every Scala Seq has.
    }
    try {
      Method size = o.getClass().getMethod("size");
      Method apply = o.getClass().getMethod("apply", int.class);
      int n = (Integer) size.invoke(o);
      String[] out = new String[n];
      for (int i = 0; i < n; i++) {
        Object v = apply.invoke(o, i);
        out[i] = (v == null) ? null : v.toString();
      }
      return out;
    } catch (ReflectiveOperationException e) {
      throw new IllegalArgumentException(
          "cannot read an array<string> argument of type " + o.getClass().getName()
              + "; add a case to SeqCoercion rather than letting it read as empty",
          e);
    }
  }

  private static Method findNoArg(Class<?> cls, String name) {
    try {
      Method m = cls.getMethod(name);
      return m.getParameterCount() == 0 ? m : null;
    } catch (NoSuchMethodException e) {
      return null;
    }
  }

  private static String[] box(Object[] in) {
    String[] out = new String[in.length];
    for (int i = 0; i < in.length; i++) {
      out[i] = (in[i] == null) ? null : in[i].toString();
    }
    return out;
  }
}
