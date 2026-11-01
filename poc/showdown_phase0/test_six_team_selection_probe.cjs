"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {run, context, validateRegistration, makeSelectedBattle, checkSelection,
  assertManifest} = require("./six_team_selection_probe.cjs");
const {moveKey} = require("./champions_move_key.cjs");

let report;
test("fixed six-member cases keep registration, selection and restoration separate", () => {
  report = run();
  assert.equal(report.schema, "phase0-six-team-selection-diagnostics-v1");
  assert.equal(report.pinned_showdown_sha, "ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
  assert.deepEqual([report.gate_A, report.gate_B, report.adoption], ["UNKNOWN", "UNSUPPORTED", "NO_GO"]);
  assert.deepEqual(report.champions_master_sha256, {
    "Pokemon_Champions_MC_move_master.csv": "39497369e1f47891da453894f256bef6c44085abea4c5c94b8288c05b31f06a5",
    "Pokemon_Champions_MC_usable_pokemon.csv": "5a2aac25586a2d45797bec18b9a81958e2eee5544beb32d39ea6cf93d720e3ee",
    "Pokemon_Champions_MC_pokemon_learnsets_linked.csv": "30305983e5ac676be27fc2f148e2326963830f2ab201845e812c93c008ebabe6",
    "Pokemon_Champions_MC_usable_held_items.csv": "fbf36aeff9906cbdf4e82bae575340d1df640187ce469ae9d31194a92faa16c8"});
  assert.equal(report.positive_case_count, 4);
  assert.equal(report.negative_case_count, 21);
  assert.equal(report.registered_team_pair_count, 1);
  assert.equal(report.selection_pattern_count, 3);
  assert.equal(report.distinct_input_state_count, 4);
  assert.equal(report.repetitions, 0);
  assert.equal(report.representative_100_cases_met, false);
  assert.deepEqual(report.counts, {PASS_SYNTHETIC: 4,
    REJECTED_INVALID_REGISTRATION: 16, REJECTED_INVALID_SELECTION: 5,
    SELECTION_MISMATCH: 0, STATE_OR_QUEUE_MISMATCH: 0, INCONCLUSIVE: 0, UNSUPPORTED: 0});
  const manifest = require("./six_team_selection_cases.json").cases;
  assert.deepEqual(report.results.map(r => [r.case_id, r.mechanism, r.status]),
    manifest.map(c => [c.id, c.mechanism, c.expected_status]));
});

test("both registered sixes pass two validators, selected three are exact, forks are independent", () => {
  const expected = [
    [["garchomp", "metagross", "venusaur"], ["metagross", "garchomp", "venusaur"]],
    [["blastoise", "garchomp", "charizard"], ["gyarados", "venusaur", "blastoise"]],
    [["metagross", "garchomp", "venusaur"], ["venusaur", "metagross", "garchomp"]],
    [["blastoise", "garchomp", "charizard"], ["gyarados", "venusaur", "blastoise"]],
  ];
  for (const [i, result] of report.results.slice(0, 4).entries()) {
    assert.equal(result.status, "PASS_SYNTHETIC", result.case_id + ": " + result.reason);
    assert.ok(result.registration.every(r => r.legal && r.agree));
    assert.deepEqual(result.preview.registered_sizes, [6, 6]);
    assert.deepEqual(result.preview.request_flags, [true, true]);
    assert.deepEqual(result.preview.requested_sizes, [3, 3]);
    assert.equal(result.preview.picked_team_size, 3);
    assert.deepEqual(result.selected.map(s => s.actual), expected[i]);
    assert.deepEqual(result.selected.map(s => s.lead), expected[i].map(team => team[0]));
    assert.ok(result.selected.every(s => s.passed && s.unselected.length === 3));
    assert.deepEqual(result.post_selection_hp_pp.map(side => side.map(p => p.species).sort()),
      expected[i].map(team => [...team].sort()));
    assert.equal(result.selection_status, "PASS");
    assert.equal(result.battle_progressed, true);
    assert.ok(result.turn_after > result.turn_before);
    assert.equal(result.restoration_status, "PASS_SYNTHETIC");
    assert.equal(result.restoration.before_sha256, result.restoration.after_sha256);
    assert.equal(result.restoration.first_difference_field, null);
    assert.ok(Object.values(result.restoration.branch_independence).every(Boolean));
    assert.ok(expected[i][0].includes(result.restoration.action_ids[1]));
    assert.notEqual(result.restoration.action_ids[1], result.active_after_progress[0]);
  }
  assert.ok(report.results[1].post_selection_hp_pp.some(side =>
    side.some(mon => mon.hp < mon.maxhp || mon.moves.some(m => m.pp < m.maxpp))));
  assert.ok(report.results[3].switch_event_count > 0);
  assert.equal(report.results[3].active_after_progress[0], "garchomp");
});

test("all registration negatives stop before Battle and keep validator disagreement visible", () => {
  for (const result of report.results.filter(r => r.case_id.startsWith("NEG-") &&
      Number(r.case_id.slice(4)) <= 16)) {
    assert.equal(result.status, "REJECTED_INVALID_REGISTRATION", result.case_id);
    assert.equal(result.rejection_stage, "CHAMPIONS_MASTER");
    assert.equal(result.registration[0].champions_master.status, "REJECTED");
    assert.equal(result.registration[1].legal, true);
    assert.equal(result.selection_status, "NOT_RUN");
    assert.equal(result.restoration_status, "NOT_RUN");
    assert.equal(result.battle_progressed, false);
  }
  const regional = report.results.find(r => r.case_id === "NEG-004");
  assert.match(regional.reason, /#0026/);
  assert.deepEqual(regional.registered_species_ids[0].slice(0, 2), ["raichu", "raichualola"]);
  const wrongStone = report.results.find(r => r.case_id === "NEG-013");
  assert.match(wrongStone.reason, /mega stone wrong species/);
  assert.equal(wrongStone.registration[0].team_validator.status, "LEGAL");
  assert.equal(wrongStone.registration[0].agree, false);
});

test("matching mega stone is accepted while wrong-owner stone records the validator split", () => {
  const ctx = context();
  const team = structuredClone(ctx.registered[0]);
  team[0].item = "Garchompite";
  assert.deepEqual(validateRegistration(team, ctx), {
    champions_master: {status: "LEGAL", reason: null},
    team_validator: {status: "LEGAL", reason: null}, agree: true, legal: true});
  team[0].item = "Lucarionite";
  const invalid = validateRegistration(team, ctx);
  assert.equal(invalid.champions_master.status, "REJECTED");
  assert.equal(invalid.team_validator.status, "LEGAL");
  assert.equal(invalid.agree, false);
});

test("selection guard rejects invalid slots, count and lead before Showdown auto-fill", () => {
  for (const result of report.results.slice(-5)) {
    assert.equal(result.status, "REJECTED_INVALID_SELECTION", result.case_id);
    assert.ok(result.registration.every(r => r.legal));
    assert.equal(result.rejection_stage, "SELECTION_GUARD");
    assert.equal(result.selection_status, "REJECTED");
    assert.equal(result.restoration_status, "NOT_RUN");
    assert.equal(result.battle_progressed, false);
  }
  assert.throws(() => checkSelection({slots: [1, 2], lead_slot: 1}, Array(6)), /exactly three/);
  assert.throws(() => checkSelection({slots: [1, 2, 3, 4], lead_slot: 1}, Array(6)), /exactly three/);
  assert.throws(() => checkSelection({slots: [4, 1, 5], lead_slot: 1}, Array(6)), /lead/);
});

test("Japanese move name and MoveKey use fixed normalization for both validators and Battle", () => {
  const ctx = context();
  const registered = ctx.registered.map(team => structuredClone(team));
  assert.equal(moveKey("じしん"), "earthquake");
  registered[0][0].moves = ["じしん"];
  registered[1][1].moves = ["earthquake"];
  assert.ok(registered.every(team => validateRegistration(team, ctx).legal));
  const selected = makeSelectedBattle(registered,
    [{slots: [1, 2, 3], lead_slot: 1}, {slots: [2, 1, 3], lead_slot: 2}]);
  assert.equal(selected.battle.p1.active[0].moveSlots[0].id, "earthquake");
  assert.equal(selected.battle.p2.active[0].moveSlots[0].id, "earthquake");
});

test("case removal or substitution cannot leave CI green", () => {
  const cases = require("./six_team_selection_cases.json").cases;
  assert.throws(() => assertManifest(cases.slice(1)), /coverage changed/);
  const substituted = structuredClone(cases);
  substituted[12].mutation = "unavailable_species";
  assert.throws(() => assertManifest(substituted), /identity or expectation changed/);
});
