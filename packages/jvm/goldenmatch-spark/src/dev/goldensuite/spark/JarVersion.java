package dev.goldensuite.spark;

import java.io.InputStream;
import java.net.URI;
import java.util.jar.Manifest;

/** Which build of this jar an executor is actually running.
 *
 * <h2>Why a jar needs to say its own name</h2>
 *
 * Until this jar was published it only ever existed inside the CI run that
 * built it, so "which one is on the cluster" had exactly one answer. Publishing
 * it changes that: an operator downloads a release asset, copies it around,
 * pins it in a job spec, and six weeks later has a file called
 * {@code goldenmatch-spark.jar} on a cluster with no way to tell which release
 * it came from. Every jar of every version has the same name.
 *
 * <p>That matters more here than in most jars because of what this one contains.
 * It carries a Rust kernel per platform, and the failure mode this arc keeps
 * paying for is the SILENT one -- a jar whose library will not load falls back
 * to the {@code exact}-only scorer and keeps returning numbers. "Which jar?" is
 * the first question of any such investigation, and an anonymous file cannot
 * answer it.
 *
 * <h2>Read from THIS class's jar, not the classpath</h2>
 *
 * The obvious implementation -- {@code getResource("/META-INF/MANIFEST.MF")} --
 * returns the FIRST manifest on the classpath, which on a Spark executor is
 * overwhelmingly likely to be Spark's own. It would report a version with
 * total confidence and be wrong. So the lookup goes through this class's own
 * resource URL and reads the manifest from inside the same archive, which is
 * the only manifest that describes this code.
 */
public final class JarVersion {

  private JarVersion() {}

  /** Sentinel for "not running from a jar" -- a source checkout, or CI's
   * self-test, which runs against loose classes before the jar is assembled.
   * Deliberately not null and not a fake version: a build that cannot identify
   * itself should say so rather than claim to be something. */
  public static final String UNKNOWN = "unknown";

  private static final String VERSION = read();

  private static String read() {
    try {
      java.net.URL self = JarVersion.class.getResource("JarVersion.class");
      if (self == null) {
        return UNKNOWN;
      }
      String url = self.toString();
      // "jar:file:/path/to/x.jar!/dev/goldensuite/spark/JarVersion.class"
      // Loose classes give a plain "file:" URL and no manifest exists.
      if (!url.startsWith("jar:")) {
        return UNKNOWN;
      }
      int bang = url.lastIndexOf('!');
      if (bang < 0) {
        return UNKNOWN;
      }
      String manifestUrl = url.substring(0, bang + 1) + "/META-INF/MANIFEST.MF";
      try (InputStream in = URI.create(manifestUrl).toURL().openStream()) {
        String v = new Manifest(in).getMainAttributes().getValue("Implementation-Version");
        return (v == null || v.isEmpty()) ? UNKNOWN : v;
      }
    } catch (Exception e) {
      // Never fatal. This is a label, and a job must not die because it could
      // not read one -- the same reasoning that makes the scorer fall back.
      return UNKNOWN;
    }
  }

  /** The {@code Implementation-Version} stamped into this jar at build time,
   * or {@link #UNKNOWN} when not running from a jar. */
  public static String version() {
    return VERSION;
  }
}
