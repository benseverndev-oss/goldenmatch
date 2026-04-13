/**
 * Transform barrel — imports all transform modules for side-effect registration.
 * Re-exports registry functions as the public API.
 */

// Side-effect imports: each module calls registerTransform() at import time.
// Order matters: auto_correct depends on categorical being registered first.
import "./text.js";
import "./phone.js";
import "./names.js";
import "./address.js";
import "./dates.js";
import "./email.js";
import "./numeric.js";
import "./categorical.js";
import "./identifiers.js";
import "./url.js";
import "./auto-correct.js";

// Re-export registry API
export {
  registerTransform,
  getTransform,
  listTransforms,
  parseTransformName,
  registry,
} from "./registry.js";
