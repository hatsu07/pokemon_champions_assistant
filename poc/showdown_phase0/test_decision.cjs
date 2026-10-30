"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {assess, REQUIRED} = require("./decision.cjs");
test("no evidence is never promoted to PASS", () => {
  const d = assess(null, null);
  assert.equal(d.gate_A_calculation, "UNKNOWN");
  assert.equal(d.gate_B_midbattle, "UNSUPPORTED");
  assert.equal(d.recommended_adoption, "NO_GO");
  assert.equal(d.performance_100_valid, false);
});
test("synthetic-only cases cannot approve adoption", () => {
  const results = Object.fromEntries(REQUIRED.map(k => [k, {status: "PASS_SYNTHETIC"}]));
  results.unknown_sets = {status: "PASS_SYNTHETIC", changed_initial_model: true};
  const d = assess({results}, {completed: 120, p99_ms: 99});
  assert.equal(d.gate_B_midbattle, "PARTIAL");
  assert.equal(d.gate_A_calculation, "UNKNOWN");
  assert.equal(d.synthetic_hypotheses_ok, true);
  assert.equal(d.performance_100_valid, true);
  assert.equal(d.recommended_adoption, "NO_GO");
});
test("partial golden evidence cannot be called confirmed", () => {
  const d = assess({}, {}, [{verified: true, matched: true}]);
  assert.equal(d.gate_A_calculation, "PARTIAL");
  assert.equal(d.recommended_adoption, "NO_GO");
});
test("missing one restoration field fails independent gate", () => {
  const results = Object.fromEntries(REQUIRED.filter(x => x !== "item_choice")
    .map(k => [k, {status: "PASS_SYNTHETIC"}]));
  const d = assess({results}, {completed: 120, p99_ms: 50});
  assert.equal(d.gate_B_midbattle, "UNSUPPORTED");
  assert.ok(d.missing_restore_cases.includes("item_choice"));
});
