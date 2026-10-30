"use strict";
// Synthetic comparison helper. It compares persisted values, not object key insertion order.
// Array order remains significant (e.g. queued actions).
const crypto = require("node:crypto");
function stableStringify(value) {
  const normalize = x => {
    if (Array.isArray(x)) return x.map(normalize);
    if (x && typeof x === "object")
      return Object.fromEntries(Object.keys(x).sort()
        .filter(k => x[k] !== undefined).map(k => [k, normalize(x[k])]));
    return x;
  };
  return JSON.stringify(normalize(value));
}
function fingerprint(value) {
  return crypto.createHash("sha256").update(stableStringify(value)).digest("hex");
}
module.exports = {stableStringify, fingerprint};
