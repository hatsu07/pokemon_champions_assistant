"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {run, validateReport, assertManifest, selectionFor, timingStats} =
  require("./representative_states_probe.cjs");
const manifest = require("./representative_states_cases.json");

let output;
test("127 separately defined M-C snapshots preserve tactical distinctions", () => {
  output = run();
  assert.deepEqual([output.gate_A, output.gate_B, output.adoption],
    ["UNKNOWN", "UNSUPPORTED", "NO_GO"]);
  assert.equal(output.case_count, 127);
  assert.equal(output.counts.PASS_SYNTHETIC, 127);
  assert.equal(output.distinct_complete_states, 127);
  assert.equal(output.distinct_tactical_states, 127);
  assert.equal(output.distinct_matchups, 18);
  assert.equal(output.distinct_mechanisms, 8);
  assert.equal(output.repetitions, 0);
  assert.equal(output.representative_100_synthetic_states_met, true);
  assert.equal(output.native_game_verification, false);
  assert.equal(output.windows_e2e_verified, false);
  assert.ok(output.results.some(r => r.setup_provenance.includes("EXECUTED_MEGA_TURN")));
  assert.ok(output.results.some(r => r.setup_provenance.includes("SYNTHETIC_HP_SETUP_THEN_ENGINE_ITEM_EVENT")));
  for (const r of output.results) {
    assert.equal(r.status, "PASS_SYNTHETIC", r.case_id + ": " + r.reason);
    assert.ok(r.branch?.distinct_outcome && r.branch?.source_unchanged &&
      r.branch?.repeated_action_identical);
    assert.notEqual(r.branch.action_ids[0], r.branch.action_ids[1]);
    assert.ok(r.timings_ms.prepare_ms >= 0);
  }
});
test("explicit manifest fails closed on removal, substitution, duplication", () => {
  assert.throws(() => assertManifest(manifest.cases.slice(1)), /coverage changed/);
  const altered = structuredClone(manifest.cases);
  altered[4].p2_active_slot = 6;
  assert.throws(() => assertManifest(altered), /coverage changed/);
  const duplicate = structuredClone(manifest.cases);
  duplicate[10] = duplicate[9];
  assert.throws(() => assertManifest(duplicate), /coverage changed/);
});
test("failed samples or tactically duplicated states cannot satisfy 100 gate", () => {
  assert.ok(output);
  const fewer = {...output, counts: {...output.counts, PASS_SYNTHETIC: 99}};
  assert.throws(() => validateReport(fewer), /regression/);
  const dupe = {...output, distinct_tactical_states: 99};
  assert.throws(() => validateReport(dupe), /regression/);
  const misplaced = {...output, results: output.results.slice(1).concat(output.results[0])};
  assert.throws(() => validateReport(misplaced), /regression/);
});
test("selection guards reject repeated slots and duration metrics are finite", () => {
  assert.throws(() => selectionFor(7), /invalid target/);
  assert.deepEqual(timingStats([1, 2, 3, 4]), {samples: 4,
    p50: 2, p95: 4, p99: 4, max: 4});
  assert.ok(output);
  for (const stage of Object.values(output.timing_ms)) {
    assert.equal(stage.samples, 127);
    assert.ok(stage.p50 <= stage.p95 && stage.p95 <= stage.p99 && stage.p99 <= stage.max);
  }
});
