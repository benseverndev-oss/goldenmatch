package dev.goldensuite.spark;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.apache.spark.sql.api.java.UDF21;

/** Score EVERY comparison field of one pair in a single call.
 *
 * <h2>Why this exists</h2>
 *
 * A profile of the distributed FS counts stage put ~87% of it in scoring, with
 * the Rust kernel itself at ~0.1s. The stage is ~94% of the whole training wall
 * at 5M rows, so scoring overhead is the wall. {@link GoldenScoreRowUdf} is
 * called once per FIELD per pair -- 5 fields over 5.5M pairs is ~27M Spark UDF
 * dispatches and ~27M JNI transitions for one blocking pass.
 *
 * <p>This collapses both layers, which are separate costs and were being
 * conflated:
 *
 * <ul>
 *   <li><b>Spark UDF dispatch</b>: 1 call per pair instead of one per field.
 *   <li><b>JNI transitions</b>: {@link GoldenScorer#score} already takes ARRAYS,
 *       so fields sharing a scorer id are scored in ONE native call. The scale
 *       fixture is 4 jaro-winkler + 1 levenshtein, so 5 transitions per pair
 *       become 2.
 * </ul>
 *
 * <h2>What this is NOT</h2>
 *
 * It is not the batching {@link GoldenScoreUdf} does. That one groups PAIRS
 * into arrays and returns a score per pair, so the plan needs
 * {@code arrays_zip} + {@code explode} to get back to rows -- and J4 measured
 * that unpack at ~14x the kernel it was avoiding, because Spark arrays are
 * {@code ArrayData} of {@code InternalRow}, not columnar vectors. Here the row
 * shape is unchanged: one pair in, one row out. The only array is the returned
 * similarity vector, which is n_fields doubles rather than a materialisation of
 * every pair.
 *
 * <h2>Bucketing stays in Catalyst, deliberately</h2>
 *
 * This returns SIMILARITIES, not levels. {@code fs_level_expr} turns a
 * similarity into a comparison level with {@code when(sim >= t, 1)} summed --
 * pure SQL, already codegen'd, and measured as part of the cheap ~7.3s baseline.
 * Moving that into Java would duplicate the one piece of FS logic that has to
 * agree across the one-box, Spark and SQL paths, to save something that is not
 * costing anything.
 *
 * <h2>Fixed arity, null-padded</h2>
 *
 * Spark's UDF interfaces are fixed-arity and Connect registers scalar UDFs, so
 * this takes 20 value slots (10 fields) and the caller passes nulls for unused
 * ones. Ugly, and the alternative is worse: an array-typed input parameter
 * reintroduces exactly the {@code ArrayData} churn documented above, on the way
 * IN this time.
 */
public final class GoldenScoreVectorUdf
    implements UDF21<
        String, String, String, String, String, String, String, String, String, String,
        String, String, String, String, String, String, String, String, String, String,
        String, List<Double>> {

  /** Max comparison fields one call can carry (20 value slots / 2). */
  public static final int MAX_FIELDS = 10;

  /** Parsed `cfg` strings. The config is identical for every row in a query, so
   *  parsing it per call would be pure waste; the map is bounded by the number
   *  of distinct matchkeys a session runs, which is small. */
  private static final Map<String, int[]> CFG_CACHE = new HashMap<>();

  private static int[] scorerIds(String cfg) {
    if (cfg == null || cfg.isEmpty()) {
      throw new IllegalArgumentException("cfg must be a comma-separated scorer id list");
    }
    synchronized (CFG_CACHE) {
      int[] hit = CFG_CACHE.get(cfg);
      if (hit != null) {
        return hit;
      }
    }
    String[] parts = cfg.split(",");
    if (parts.length > MAX_FIELDS) {
      throw new IllegalArgumentException(
          "cfg names " + parts.length + " fields; this UDF carries at most " + MAX_FIELDS);
    }
    int[] ids = new int[parts.length];
    for (int i = 0; i < parts.length; i++) {
      ids[i] = Integer.parseInt(parts[i].trim());
    }
    synchronized (CFG_CACHE) {
      CFG_CACHE.put(cfg, ids);
    }
    return ids;
  }

  @Override
  public List<Double> call(
      String cfg,
      String a0, String b0, String a1, String b1, String a2, String b2,
      String a3, String b3, String a4, String b4, String a5, String b5,
      String a6, String b6, String a7, String b7, String a8, String b8,
      String a9, String b9) {
    int[] ids = scorerIds(cfg);
    String[] av = {a0, a1, a2, a3, a4, a5, a6, a7, a8, a9};
    String[] bv = {b0, b1, b2, b3, b4, b5, b6, b7, b8, b9};

    // Group field indices by scorer id, so fields sharing a scorer cross into
    // native ONCE. Insertion order is irrelevant -- results are written back to
    // the field's own slot.
    Map<Integer, List<Integer>> byScorer = new HashMap<>();
    for (int i = 0; i < ids.length; i++) {
      byScorer.computeIfAbsent(ids[i], k -> new ArrayList<>()).add(i);
    }

    Double[] out = new Double[ids.length];
    GoldenScorer scorer = ScorerSelection.scorer();
    for (Map.Entry<Integer, List<Integer>> e : byScorer.entrySet()) {
      List<Integer> idx = e.getValue();
      String[] a = new String[idx.size()];
      String[] b = new String[idx.size()];
      for (int k = 0; k < idx.size(); k++) {
        a[k] = av[idx.get(k)];
        b[k] = bv[idx.get(k)];
      }
      Double[] scores = scorer.score(e.getKey(), a, b);
      for (int k = 0; k < idx.size(); k++) {
        // A null score means "not comparable" and must stay null: the kernel
        // maps a missing value to "" and would score null-vs-null as a perfect
        // 1.0. Same contract as GoldenScoreRowUdf.
        out[idx.get(k)] = (scores == null || k >= scores.length) ? null : scores[k];
      }
    }

    List<Double> result = new ArrayList<>(ids.length);
    for (Double d : out) {
      result.add(d);
    }
    return result;
  }
}
