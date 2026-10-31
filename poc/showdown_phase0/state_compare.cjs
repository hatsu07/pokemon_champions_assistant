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
// Showdown inserts wall-clock seconds into the protocol log at the start of
// each turn (|t:|<unix-seconds>). It is not part of the battle mechanics.
// Normalize ONLY this exact line for deterministic replay fingerprints.
// Preserve every other log entry and do not mutate source observations.
function normalizeReplayTimestamps(state) {
  if (!state || typeof state !== "object" || !Array.isArray(state.log)) return state;
  return {
    ...state,
    log: state.log.map(line =>
      typeof line === "string" && /^\|t:\|\d+$/.test(line)
        ? "|t:|<wallclock>"
        : line),
  };
}

// Diagnose serialized diff without treating log-only mismatches as a full pass.
// Log text can still carry tactically meaningful events: all mismatches fail.
function classifySerializedDivergence(a, b) {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  const tacticalA = {}, tacticalB = {};
  for (const k of keys) if (k !== "log") {
    tacticalA[k] = a?.[k]; tacticalB[k] = b?.[k];
  }
  return {
    kind: fingerprint(tacticalA) === fingerprint(tacticalB) ? "LOG_ONLY" : "STATE_OR_QUEUE",
    full_state_equal: fingerprint(a) === fingerprint(b),
    non_log_equal: fingerprint(tacticalA) === fingerprint(tacticalB),
    log_equal: fingerprint(a?.log || []) === fingerprint(b?.log || []),
  };
}
module.exports = {stableStringify, fingerprint, normalizeReplayTimestamps, classifySerializedDivergence};
