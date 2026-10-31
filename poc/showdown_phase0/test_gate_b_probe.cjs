"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {run, assertManifest, GAP_FIELDS} = require("./gate_b_probe.cjs");
const {fingerprint, classifySerializedDivergence} = require("./state_compare.cjs");

let report;
test("fixed Gate B coverage and separate B1/B2 diagnostic outcomes", () => {
  report = run();
  assert.equal(report.case_count, 14);
  assert.equal(report.mechanism_count, 14);
  assert.equal(report.unique_input_state_count, 11, "repeated states are counted once");
  assert.equal(Object.keys(report.champions_master_sha256).length, 4);
  assert.ok(Object.values(report.champions_master_sha256).every(value => /^[a-f0-9]{64}$/.test(value)));
  assert.deepEqual([report.gate_A, report.gate_B, report.adoption], ["UNKNOWN", "UNSUPPORTED", "NO_GO"]);
  assert.equal(report.counts.B1.PASS_SYNTHETIC, 12);
  assert.equal(report.counts.B2.INCONCLUSIVE, 2);
  assert.equal(report.counts.B2.PASS_SYNTHETIC, 0);
  assert.ok(report.results.every((r, i) => r.case_id ===
    `${i < 12 ? "B1" : "B2"}-${String(i + 1).padStart(2, "0")}`));
  for (const result of report.results.filter(x => x.phase === "B1")) {
    assert.equal(result.status, "PASS_SYNTHETIC", result.case_id + ": " + result.reason);
    assert.equal(result.first_difference_field, null);
    assert.ok(result.input_state_sha256 && result.restore_source_state && result.restored_state);
    assert.equal(result.branch_independence.repeat_reproduced, true);
    assert.equal(result.branch_independence.distinct_results, true);
    assert.equal(result.branch_independence.source_unchanged, true);
    assert.notEqual(result.action_ids[0], result.action_ids[1]);
    assert.ok(Object.values(result.timing_ms).every(Number.isFinite));
  }
  for (const result of report.results.filter(x => x.phase === "B2")) {
    assert.equal(result.restored_state, null);
    assert.deepEqual(result.unsupported_fields, GAP_FIELDS);
    assert.ok(Object.values(result.restore_source_state.unknown).every(v => v === "UNKNOWN"));
    assert.equal(result.same_observation, true);
    assert.equal(result.multiple_consistent_models, true);
  }
  const hypothesis = report.results.at(-1).continuation_result;
  assert.notEqual(...hypothesis.opponent_move_ids);
  assert.equal(hypothesis.distinct_outputs, true);
  assert.equal(hypothesis.source_states_unchanged, true);
});

test("missing or substituted required case fails coverage before execution", () => {
  const manifest = require("./gate_b_cases.json").cases;
  assert.throws(() => assertManifest(manifest.slice(0, -1)), /coverage changed/);
  const changed = structuredClone(manifest);
  changed[5].mechanism = "item_present";
  assert.throws(() => assertManifest(changed), /coverage changed/);
});

test("real Showdown snapshots detect each dangerous field corruption", () => {
  assert.ok(report, "the coverage test must run first");
  const cases = Object.fromEntries(report.results.map(x => [x.case_id, x.restore_source_state]));
  const controls = [
    ["B1-03", s => {s.sides[0].pokemon[0].moveSlots[0].pp--;}, "STATE_OR_QUEUE"],
    ["B1-07", s => {s.sides[0].pokemon[0].volatiles.choicelock.move = "dragonclaw";}, "STATE_OR_QUEUE"],
    ["B1-01", s => {s.prng = "1,2,3,4";}, "STATE_OR_QUEUE"],
    ["B1-09", s => {s.sides[0].sideConditions.reflect.duration--;}, "STATE_OR_QUEUE"],
    ["B1-09", s => {s.field.weatherState.duration--;}, "STATE_OR_QUEUE"],
    ["B1-06", s => {s.sides[1].pokemon[0].item = "sitrusberry";}, "STATE_OR_QUEUE"],
    ["B1-10", s => {s.sides[1].pokemon[2].species = "lucario";}, "STATE_OR_QUEUE"],
    ["B1-12", s => {s.sides[0].choice.actions = [];}, "STATE_OR_QUEUE"],
    ["B1-12", s => {s.queue.push({choice: "move", moveid: "earthquake"});}, "STATE_OR_QUEUE"],
    ["B1-10", s => {s.log = s.log.filter(line => !line.startsWith("|-mega|"));}, "LOG_ONLY"],
  ];
  for (const [id, mutate, expected] of controls) {
    const original = cases[id], changed = structuredClone(original);
    mutate(changed);
    assert.notEqual(fingerprint(original), fingerprint(changed), id + " corruption was masked");
    assert.equal(classifySerializedDivergence(original, changed).kind, expected, id);
  }
  const partial = cases["B2-13"], altered = structuredClone(partial);
  altered.unknown.opponent_unrevealed_item = "sitrusberry";
  assert.notEqual(fingerprint(partial), fingerprint(altered), "unknown must not become observed");
});
