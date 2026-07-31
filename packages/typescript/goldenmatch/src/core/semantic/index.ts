/**
 * Semantic-layer interoperability (edge-safe subset).
 *
 * GoldenMatch produces the resolved, conformed entity keys a semantic layer's
 * joins and measures silently assume. Today the TS port ships the key-integrity
 * certifier + the Customer 360 serving-join certificate; the dialect catalog
 * emitters (MetricFlow / Cube / OSI) remain Python-only.
 */
export {
  KeyIntegrityCertificate,
  certifyKeyIntegrity,
  type KeyIntegrityCertificateInit,
} from "./keyIntegrity.js";
export { ServingJoinCertificate, certifyServingJoins } from "./serving.js";
