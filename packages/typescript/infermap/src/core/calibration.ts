// Confidence calibration — post-assignment transform of raw scores into
// calibrated probabilities. Mirrors infermap/calibration.py.
//
// A calibrator does NOT change which mappings the engine picks; it only
// relabels the confidence attached to each picked mapping. JSON round-trip
// uses a `kind` discriminator for portability between Python and JS.

export interface CalibratorJSON {
  kind: string;
  [k: string]: unknown;
}

export interface Calibrator {
  readonly kind: string;
  fit(scores: readonly number[], correct: readonly number[]): void;
  transform(scores: readonly number[]): number[];
  toJSON(): CalibratorJSON;
}

// ---------- Identity ----------

export class IdentityCalibrator implements Calibrator {
  readonly kind = "identity";
  fit(_scores: readonly number[], _correct: readonly number[]): void {
    // no-op
  }
  transform(scores: readonly number[]): number[] {
    return scores.map((s) => s);
  }
  toJSON(): CalibratorJSON {
    return { kind: this.kind };
  }
  static fromJSON(_obj: CalibratorJSON): IdentityCalibrator {
    return new IdentityCalibrator();
  }
}

// ---------- Isotonic (pool-adjacent-violators) ----------

export class IsotonicCalibrator implements Calibrator {
  readonly kind = "isotonic";
  private _x: number[];
  private _y: number[];

  constructor(x: number[] = [0, 1], y: number[] = [0, 1]) {
    this._x = x.slice();
    this._y = y.slice();
  }

  fit(scores: readonly number[], correct: readonly number[]): void {
    const n = scores.length;
    if (n === 0) return;
    // Stable sort by score ascending.
    const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => {
      const d = scores[a]! - scores[b]!;
      return d !== 0 ? d : a - b;
    });
    const xs = order.map((i) => scores[i]!);
    const ys = order.map((i) => correct[i]!);

    // Pool-adjacent-violators.
    const vals: number[] = [];
    const wts: number[] = [];
    for (const y of ys) {
      vals.push(y);
      wts.push(1);
      while (vals.length >= 2 && vals[vals.length - 2]! > vals[vals.length - 1]!) {
        const v2 = vals.pop()!;
        const w2 = wts.pop()!;
        const v1 = vals.pop()!;
        const w1 = wts.pop()!;
        vals.push((v1 * w1 + v2 * w2) / (w1 + w2));
        wts.push(w1 + w2);
      }
    }

    // Expand pooled values back to per-point fitted ys.
    const fitted = new Array<number>(n);
    let idx = 0;
    for (let b = 0; b < vals.length; b++) {
      const k = Math.round(wts[b]!);
      for (let j = 0; j < k; j++) fitted[idx++] = vals[b]!;
    }

    // Dedup by xs: average fitted values for identical xs.
    const uniqX: number[] = [];
    const uniqY: number[] = [];
    const counts: number[] = [];
    for (let i = 0; i < n; i++) {
      const xv = xs[i]!;
      if (uniqX.length > 0 && uniqX[uniqX.length - 1] === xv) {
        uniqY[uniqY.length - 1]! += fitted[i]!;
        counts[counts.length - 1]! += 1;
      } else {
        uniqX.push(xv);
        uniqY.push(fitted[i]!);
        counts.push(1);
      }
    }
    for (let i = 0; i < uniqY.length; i++) uniqY[i] = uniqY[i]! / counts[i]!;

    // Enforce monotonicity (defensive) and clip to [0, 1].
    for (let i = 1; i < uniqY.length; i++) {
      if (uniqY[i]! < uniqY[i - 1]!) uniqY[i] = uniqY[i - 1]!;
    }
    for (let i = 0; i < uniqY.length; i++) {
      uniqY[i] = Math.max(0, Math.min(1, uniqY[i]!));
    }

    this._x = uniqX;
    this._y = uniqY;
  }

  transform(scores: readonly number[]): number[] {
    if (this._x.length === 0) return scores.map((s) => s);
    const out = new Array<number>(scores.length);
    const x = this._x;
    const y = this._y;
    const last = x.length - 1;
    for (let i = 0; i < scores.length; i++) {
      const s = scores[i]!;
      if (s <= x[0]!) {
        out[i] = y[0]!;
      } else if (s >= x[last]!) {
        out[i] = y[last]!;
      } else {
        // Binary search.
        let lo = 0;
        let hi = last;
        while (lo + 1 < hi) {
          const mid = (lo + hi) >> 1;
          if (x[mid]! <= s) lo = mid;
          else hi = mid;
        }
        const x0 = x[lo]!;
        const x1 = x[hi]!;
        const y0 = y[lo]!;
        const y1 = y[hi]!;
        out[i] = x1 === x0 ? y0 : y0 + ((s - x0) * (y1 - y0)) / (x1 - x0);
      }
    }
    return out;
  }

  toJSON(): CalibratorJSON {
    return { kind: this.kind, x: this._x.slice(), y: this._y.slice() };
  }

  static fromJSON(obj: CalibratorJSON): IsotonicCalibrator {
    if (!Array.isArray(obj["x"]) || !Array.isArray(obj["y"])) {
      throw new Error(
        "IsotonicCalibrator.fromJSON: missing or non-array 'x'/'y' fields"
      );
    }
    const x = (obj["x"] as number[]).map(Number);
    const y = (obj["y"] as number[]).map(Number);
    if (x.length !== y.length) {
      throw new Error(
        `IsotonicCalibrator.fromJSON: x.length (${x.length}) !== y.length (${y.length})`
      );
    }
    return new IsotonicCalibrator(x, y);
  }
}

// ---------- Platt (sigmoid) ----------

function logSigmoid(z: number): number {
  // -log(1 + exp(-z)) numerically stable
  if (z >= 0) return -Math.log1p(Math.exp(-z));
  return z - Math.log1p(Math.exp(z));
}

export class PlattCalibrator implements Calibrator {
  readonly kind = "platt";
  private _a: number;
  private _b: number;

  get a(): number { return this._a; }
  get b(): number { return this._b; }

  constructor(a = 1.0, b = 0.0) {
    this._a = a;
    this._b = b;
  }

  fit(scores: readonly number[], correct: readonly number[]): void {
    if (scores.length === 0) return;
    // Minimize binary cross-entropy over (a, b) via Nelder-Mead.
    const nll = (params: [number, number]): number => {
      const [a, b] = params;
      let total = 0;
      for (let i = 0; i < scores.length; i++) {
        const z = a * scores[i]! + b;
        const c = correct[i]!;
        // c * log sigmoid(z) + (1-c) * log(1-sigmoid(z))
        // log(1-sigmoid(z)) = logSigmoid(-z)
        total += c * logSigmoid(z) + (1 - c) * logSigmoid(-z);
      }
      return -total;
    };
    const [a, b] = nelderMead2D(nll, [1.0, 0.0], 1e-6, 500);
    this._a = a;
    this._b = b;
  }

  transform(scores: readonly number[]): number[] {
    const out = new Array<number>(scores.length);
    for (let i = 0; i < scores.length; i++) {
      const z = this._a * scores[i]! + this._b;
      // Numerically stable sigmoid.
      out[i] = z >= 0 ? 1 / (1 + Math.exp(-z)) : Math.exp(z) / (1 + Math.exp(z));
    }
    return out;
  }

  toJSON(): CalibratorJSON {
    return { kind: this.kind, a: this._a, b: this._b };
  }

  static fromJSON(obj: CalibratorJSON): PlattCalibrator {
    if (typeof obj["a"] !== "number" || typeof obj["b"] !== "number") {
      throw new Error(
        "PlattCalibrator.fromJSON: missing or non-number 'a'/'b' fields"
      );
    }
    return new PlattCalibrator(obj["a"], obj["b"]);
  }
}

/**
 * Minimal Nelder-Mead for 2D functions. Used by Platt calibration because
 * scipy.optimize isn't available in JS. Converges to `tol` or `maxIter`,
 * whichever first.
 */
function nelderMead2D(
  f: (p: [number, number]) => number,
  x0: [number, number],
  tol: number,
  maxIter: number
): [number, number] {
  // Build an initial simplex.
  const step = 0.1;
  type P = [number, number];
  const simplex: P[] = [
    [x0[0], x0[1]],
    [x0[0] + step, x0[1]],
    [x0[0], x0[1] + step],
  ];
  let fs = simplex.map((p) => f(p));

  const alpha = 1;
  const gamma = 2;
  const rho = 0.5;
  const sigma = 0.5;

  for (let iter = 0; iter < maxIter; iter++) {
    // Sort by f ascending.
    const idx = [0, 1, 2].sort((a, b) => fs[a]! - fs[b]!);
    const s: P[] = [simplex[idx[0]!]!, simplex[idx[1]!]!, simplex[idx[2]!]!];
    const fsorted = [fs[idx[0]!]!, fs[idx[1]!]!, fs[idx[2]!]!];
    simplex[0] = s[0]!;
    simplex[1] = s[1]!;
    simplex[2] = s[2]!;
    fs = fsorted;

    // Convergence: range of f values and simplex size.
    const fRange = Math.abs(fs[2]! - fs[0]!);
    const sizeX = Math.max(
      Math.abs(simplex[1]![0] - simplex[0]![0]),
      Math.abs(simplex[2]![0] - simplex[0]![0])
    );
    const sizeY = Math.max(
      Math.abs(simplex[1]![1] - simplex[0]![1]),
      Math.abs(simplex[2]![1] - simplex[0]![1])
    );
    if (fRange < tol && sizeX < tol && sizeY < tol) break;

    // Centroid of best two.
    const cx = (simplex[0]![0] + simplex[1]![0]) / 2;
    const cy = (simplex[0]![1] + simplex[1]![1]) / 2;

    // Reflection.
    const xr: P = [
      cx + alpha * (cx - simplex[2]![0]),
      cy + alpha * (cy - simplex[2]![1]),
    ];
    const fr = f(xr);
    if (fr < fs[1]! && fr >= fs[0]!) {
      simplex[2] = xr;
      fs[2] = fr;
      continue;
    }
    // Expansion.
    if (fr < fs[0]!) {
      const xe: P = [cx + gamma * (xr[0] - cx), cy + gamma * (xr[1] - cy)];
      const fe = f(xe);
      if (fe < fr) {
        simplex[2] = xe;
        fs[2] = fe;
      } else {
        simplex[2] = xr;
        fs[2] = fr;
      }
      continue;
    }
    // Contraction.
    const xc: P = [
      cx + rho * (simplex[2]![0] - cx),
      cy + rho * (simplex[2]![1] - cy),
    ];
    const fc = f(xc);
    if (fc < fs[2]!) {
      simplex[2] = xc;
      fs[2] = fc;
      continue;
    }
    // Shrink.
    for (let i = 1; i < 3; i++) {
      simplex[i] = [
        simplex[0]![0] + sigma * (simplex[i]![0] - simplex[0]![0]),
        simplex[0]![1] + sigma * (simplex[i]![1] - simplex[0]![1]),
      ];
      fs[i] = f(simplex[i]!);
    }
  }
  return simplex[0]!;
}

// ---------- registry / JSON helpers ----------

export function loadCalibrator(obj: CalibratorJSON | string): Calibrator {
  const parsed: CalibratorJSON = typeof obj === "string" ? JSON.parse(obj) : obj;
  switch (parsed.kind) {
    case "identity":
      return IdentityCalibrator.fromJSON(parsed);
    case "isotonic":
      return IsotonicCalibrator.fromJSON(parsed);
    case "platt":
      return PlattCalibrator.fromJSON(parsed);
    default:
      throw new Error(`Unknown calibrator kind: ${String(parsed.kind)}`);
  }
}

export function saveCalibrator(cal: Calibrator): CalibratorJSON {
  return cal.toJSON();
}
