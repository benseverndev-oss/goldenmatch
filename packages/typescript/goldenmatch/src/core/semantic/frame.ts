/**
 * The column-oriented frame shape the semantic certifiers read.
 *
 * `certifyKeyIntegrity` takes `{ column: values[] }` — the edge-safe analogue of
 * the Arrow table / dict the Python fns take. `certifySemanticModel` maps each
 * model/dataset/cube name to one such frame.
 */

/** One table: column name -> values (every column the same length). */
export type SemanticFrame = Readonly<Record<string, readonly unknown[]>>;

/** Maps a model / dataset / cube name to the table backing it. */
export type SemanticFrames = Readonly<Record<string, SemanticFrame>>;
