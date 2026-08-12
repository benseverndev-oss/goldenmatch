// Reports the RUNTIME Java type Spark hands a Java UDF for a given column.
//
// This exists because the answer is version-dependent and guessing it wastes a
// CI round: Spark has historically passed `scala.collection.mutable.WrappedArray`
// for an ArrayType column to a Java UDF, and newer versions may pass a
// `java.util.List`. Declaring the wrong one gives a ClassCastException, which
// says nothing about whether the approach is viable -- it only says the guess
// was wrong.
//
// Taking `Object` and returning the class name turns that guess into a measured
// fact, and the array UDF next to it can then coerce defensively with the answer
// in hand.
package dev.goldensuite.probe;

import org.apache.spark.sql.api.java.UDF1;

public class GoldenTypeProbeUdf implements UDF1<Object, String> {
  @Override
  public String call(Object a) {
    if (a == null) {
      return "null";
    }
    StringBuilder sb = new StringBuilder(a.getClass().getName());
    // Interfaces matter more than the concrete class here: what a caller can
    // safely declare is an interface the value implements.
    if (a instanceof java.util.List) {
      sb.append(" [implements java.util.List]");
    }
    if (a instanceof Object[]) {
      sb.append(" [is Object[]]");
    }
    return sb.toString();
  }
}
