/**
 * Regression guard for the url_canonical ReDoS hardening (CodeQL #303).
 *
 * URL_RE previously paired `(?<host>[^/?#]+)` with `(?<rest>.*)` -- two
 * adjacent quantifiers matching the same characters, which CodeQL flags as
 * polynomial-ReDoS. The hardened form requires `rest` to start with a
 * delimiter, so the boundary is unambiguous (linear). These tests pin the
 * two things that change could break: bare `scheme://host` (the `rest`-absent
 * case) and that a large adversarial input stays fast.
 */
import { describe, expect, it } from "vitest";

import { UrlCanonicalStrategy } from "../../src/core/plugins/builtin/format.js";

const strat = new UrlCanonicalStrategy();
const canon = (u: string): unknown => strat.merge([u])[0];

describe("url_canonical ReDoS hardening", () => {
  it("canonicalizes a bare scheme://host with no path (rest absent)", () => {
    expect(canon("HTTP://Example.COM")).toBe("https://example.com");
  });

  it("strips a lone trailing slash and lowercases the host", () => {
    expect(canon("http://Example.COM/")).toBe("https://example.com");
  });

  it("preserves path/query/fragment after the delimiter", () => {
    expect(canon("https://X.com/p?a=1#f")).toBe("https://x.com/p?a=1#f");
  });

  it("returns non-URL input unchanged", () => {
    expect(canon("not a url")).toBe("not a url");
  });

  it("stays linear on a large adversarial input", () => {
    const adversarial = `a://${'"'.repeat(200_000)}\n`;
    const t0 = performance.now();
    canon(adversarial);
    expect(performance.now() - t0).toBeLessThan(500);
  });
});
