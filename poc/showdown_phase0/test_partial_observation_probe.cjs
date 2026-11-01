"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {run, assertManifest, projectObservation, assertObservation, makeCandidates,
  applyEvent, selectedTeams, syntheticTruth, FIELDS} = require("./partial_observation_probe.cjs");
const {context} = require("./six_team_selection_probe.cjs");
const {restoreChecked, state} = require("./gate_b_probe.cjs");
const {fingerprint} = require("./state_compare.cjs");
const manifest = require("./partial_observation_cases.json");

let report;
const result = id => report.results.find(x => x.case_id === id);
test("fixed 26-case partial diagnostic separates hypothesis consistency from restoration", () => {
  report = run();
  assert.equal(report.schema, "phase0-partial-observation-diagnostics-v1");
  assert.equal(report.pinned_showdown_sha, "ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
  assert.deepEqual([report.gate_A, report.gate_B, report.adoption], ["UNKNOWN", "UNSUPPORTED", "NO_GO"]);
  assert.equal(report.case_count, 26);
  assert.equal(report.mechanism_count, 26);
  assert.deepEqual(report.counts, {CONSISTENT_HYPOTHESES: 10,
    CONTRADICTORY_OBSERVATION: 1, INCONCLUSIVE: 6, UNSUPPORTED: 2,
    SYNTHETIC_TEST_PASS: 7});
  assert.deepEqual(report.results.map(x => [x.case_id, x.mechanism, x.status]),
    manifest.cases.map(x => [x.id, x.mechanism, x.expected_status]));
  assert.equal(report.repetitions, 0);
  assert.equal(report.representative_100_cases_met, false);
  assert.ok(report.distinct_complete_truth_state_count >= 4);
  assert.ok(report.distinct_partial_observation_count >= 4);
  assert.ok(report.distinct_partial_observation_count < report.case_count,
    "case-specific evidence IDs must not inflate distinct partial-state coverage");
  assert.ok(report.distinct_candidate_state_hash_count >= 4);
  assert.ok(report.results.every(x => x.complete_snapshot_restorable_from_observation === false &&
    x.unique_truth_restoration === false && x.missing_internal_state.includes("internal_rng")));
});

test("UNKNOWN, observed absence and inferred values remain distinct after JSON round-trip", () => {
  const a = result("PO-02"), consumed = result("PO-10");
  assert.deepEqual(a.observation.facts.opponent_item, {source: "UNKNOWN"});
  assert.equal(consumed.observation.facts.opponent_item.value, "");
  assert.equal(consumed.observation.facts.opponent_item.source, "SYNTHETIC");
  for (const id of ["PO-01", "PO-04", "PO-05", "PO-06", "PO-07", "PO-08", "PO-09", "PO-13"]) {
    const r = result(id);
    assert.equal(r.observation.origin, "SYNTHETIC");
    assert.equal(r.observation.verification, "UNVERIFIED");
    assert.ok(Object.values(r.observation.facts).every(f => f.source !== "GAME_OBSERVED"));
    assert.deepEqual(r.observation.facts.opponent_hp_exact, {source: "UNKNOWN"});
    assert.deepEqual(r.observation.facts.opponent_remaining_pp, {source: "UNKNOWN"});
    assert.deepEqual(r.observation.facts.opponent_boosts, {source: "UNKNOWN"});
    assert.deepEqual(r.observation.facts.opponent_mega_history, {source: "UNKNOWN"});
    assert.equal(fingerprint(JSON.parse(JSON.stringify(r.observation))), r.observation_sha256);
  }
  const model = a.candidates.find(c => c.id === "A").model;
  assert.equal(model.opponent_item.source, "INFERRED");
  assert.equal(model.opponent_item.value, "");
  assert.equal(model.opponent_remaining_pp.source, "INFERRED");
  assert.equal(JSON.parse(JSON.stringify(model)).opponent_moves.source, "INFERRED");
  assert.deepEqual(result("PO-09").observation.facts.screen_remaining_turns, {source: "UNKNOWN"});
});

test("four legal templates have independent complete synthetic states and differing continuations", () => {
  const r = result("PO-14");
  assert.equal(r.legal_hypothesis_count, 4);
  assert.deepEqual(r.candidates.map(c => c.id), ["A", "B", "C", "D"]);
  assert.equal(new Set(r.candidates.map(c => c.state_sha256)).size, 4);
  assert.ok(r.candidates.every(c => c.synthetic_roundtrip &&
    Object.values(c.model).filter(v => v?.source).every(v => v.source !== "GAME_OBSERVED")));
  assert.equal(r.candidate_independence, true);
  assert.equal(new Set(r.candidate_continuations.map(c => c.result_sha256)).size > 1, true);
  assert.ok(r.candidate_continuations.every(c => c.status === "SYNTHETIC_HYPOTHESIS_ONLY" &&
    c.action.join("/") === "move 1/move 1"));
  assert.ok(r.candidate_truth_hash_matches >= 1);
  assert.equal(r.unique_truth_restoration, false);
});

test("observed move, item, ability, consumption and Mega filter candidates without promotion", () => {
  for (const [id, candidateId, field] of [
    ["PO-17", "B", "opponent_ability"], ["PO-18", "D", "opponent_mega_state"],
    ["PO-21", "B", "opponent_revealed_moves"], ["PO-22", "B", "opponent_item"],
    ["PO-23", "B", "opponent_consumed_item"]]) {
    const r = result(id);
    assert.deepEqual(r.candidates.map(c => c.id), [candidateId]);
    assert.equal(r.observation.facts[field].source, "SYNTHETIC");
    assert.ok(r.observation.facts[field].evidence_ids[0].startsWith("synthetic-event-"));
    assert.equal(r.candidate_continuations[0].status, "NOT_RUN_CURRENT_STATE");
    assert.equal(r.candidate_truth_hash_matches, null,
      "pre-update truth must not be compared as the current post-event state");
    assert.equal(r.unique_truth_restoration, false);
  }
  assert.equal(result("PO-23").observation.facts.opponent_item.value, "");
  assert.deepEqual(result("PO-18").observation.facts.opponent_mega_history, {source: "UNKNOWN"});
});

test("switch removes old active assumptions and known Choice lock", () => {
  const r = result("PO-19"), model = r.candidates[0].model;
  assert.deepEqual(r.candidates.map(c => c.id), ["C"]);
  assert.equal(r.legal_hypothesis_count, null);
  assert.equal(r.current_active_hypothesis_count, null);
  assert.equal(r.previous_active_hypothesis_count, 1);
  assert.equal(r.hypothesis_set_scope, "PREVIOUS_ACTIVE_ONLY");
  assert.equal(r.candidates[0].state_sha256_scope, "BASE_PREVIOUS_ACTIVE_HYPOTHESIS_ONLY");
  assert.equal(r.observation.facts.active_opponent.value, "metagross");
  assert.equal(r.observation.facts.choice_lock.value, "");
  assert.deepEqual(r.observation.facts.opponent_item, {source: "UNKNOWN"});
  assert.deepEqual(model.opponent_item, {source: "UNKNOWN"});
  assert.deepEqual(model.opponent_moves, {source: "UNKNOWN"});
  assert.equal(model.previous_opponent_set.opponent_item.value, "choicescarf");
  assert.equal(model.previous_opponent_set.choice_lock.value, "earthquake");
});

test("Mega Garchomp uses the current form's Champions ability slot", () => {
  const ctx = context(), entry = {id: "MEGA-ABILITY", truth: "D"};
  const obs = projectObservation(syntheticTruth(ctx, entry), ctx, entry);
  const fact = value => ({source: "SYNTHETIC", value, evidence_ids: ["fixture-MEGA-ABILITY"]});
  obs.facts.opponent_ability = fact("sandforce");
  assert.throws(() => assertObservation(obs, ctx), /ability absent/,
    "base Garchomp cannot have Sand Force");
  obs.facts.opponent_ability = fact("roughskin");
  assert.equal(assertObservation(obs, ctx).consistent, true);
  let candidates = makeCandidates(obs, ctx).candidates;
  assert.deepEqual(candidates.map(c => c.id), ["A", "C", "D"]);
  ({candidates} = applyEvent(obs, candidates, {type: "mega_seen", value: true}, ctx, 1));
  assert.deepEqual(candidates.map(c => c.id), ["D"]);
  assert.deepEqual(obs.facts.opponent_ability, {source: "UNKNOWN"});
  assert.deepEqual(candidates[0].model.opponent_ability, {source: "UNKNOWN"});
  assert.equal(candidates[0].model.previous_form_ability.value, "roughskin");
  assert.equal(obs.facts.opponent_mega_history.value.pre_mega_ability, "roughskin");
  assert.equal(assertObservation(obs, ctx).consistent, true);
  assert.deepEqual(makeCandidates(obs, ctx).candidates.map(c => c.id), ["D"],
    "a projected Mega form with undisclosed ability stays a legal unknown");
  obs.facts.opponent_ability = fact("roughskin");
  assert.throws(() => assertObservation(obs, ctx), /ability absent/,
    "Mega Garchomp cannot retain the base form's current ability");
  obs.facts.opponent_ability = {source: "UNKNOWN"};
  ({candidates} = applyEvent(obs, candidates, {type: "ability_revealed", value: "sandforce"}, ctx, 2));
  assert.equal(obs.facts.opponent_ability.value, "sandforce");
  assert.equal(candidates[0].model.opponent_ability.value, "sandforce");
  assert.equal(candidates[0].model.opponent_ability.source, "SYNTHETIC");
  assert.equal(candidates[0].model.previous_form_ability.value, "roughskin");
  assert.equal(assertObservation(obs, ctx).consistent, true);
  const direct = makeCandidates(obs, ctx).candidates;
  assert.deepEqual(direct.map(c => c.id), ["D"],
    "a direct current-form observation must accept Sand Force");
  assert.equal(direct[0].model.opponent_ability.value, "sandforce");
  const unsupported = structuredClone(obs);
  unsupported.facts.opponent_form = fact("Garchomp-Mega-Z");
  unsupported.facts.active_opponent = fact("Garchomp-Mega-Z");
  assert.throws(() => assertObservation(unsupported, ctx), /unsupported current Mega form ability mapping/);
});

test("empty candidate set and contradictory facts never become a successful restoration", () => {
  for (const id of ["PO-15", "PO-16"]) {
    const r = result(id);
    assert.equal(r.legal_hypothesis_count, 0);
    assert.equal(r.status, "INCONCLUSIVE");
    assert.equal(r.candidate_continuations.length, 0);
    assert.equal(r.observation_consistent, true);
  }
  const c = result("PO-20");
  assert.equal(c.status, "CONTRADICTORY_OBSERVATION");
  assert.match(c.contradiction, /opponent_item/);
  assert.equal(c.legal_hypothesis_count, 0);
  assert.equal(c.candidate_continuations.length, 0);
  assert.equal(c.unique_truth_restoration, false);
  assert.equal(result("PO-26").unevaluated_candidate_count, 2);
  assert.equal(result("PO-26").legal_hypothesis_count, 2);
  assert.equal(result("PO-25").observation.facts.speed_relation.source, "INFERRED");
  assert.equal(result("PO-25").legal_hypothesis_count, 4);
  for (const id of ["PO-11", "PO-12"]) assert.equal(result(id).status, "UNSUPPORTED");
});

test("actual input validation rejects false certainty and illegal Champions facts", () => {
  const ctx = context();
  const base = projectObservation(syntheticTruth(ctx, {id: "DIRECT", truth: "A"}), ctx,
    {id: "DIRECT", truth: "A"});
  assert.equal(Object.keys(base.facts).length, FIELDS.length);
  const check = (change, pattern) => {
    const obs = structuredClone(base);
    change(obs);
    assert.throws(() => assertObservation(obs, ctx), pattern);
  };
  check(o => { o.facts.opponent_item = {source: "UNKNOWN", value: ""}; }, /UNKNOWN cannot carry/);
  check(o => { o.facts.opponent_boosts = {source: "UNKNOWN", value: {atk: 0}}; }, /UNKNOWN cannot carry/);
  check(o => { o.facts.opponent_remaining_pp = {source: "UNKNOWN", value: 5}; }, /UNKNOWN cannot carry/);
  check(o => { o.facts.opponent_revealed_moves = {source: "GAME_OBSERVED", value: ["earthquake"], evidence_ids: ["fixture-DIRECT"]}; }, /game observation lacks verified/);
  check(o => { o.facts.opponent_revealed_moves = {source: "INFERRED", value: ["earthquake"]}; }, /inference requires reason/);
  check(o => { o.facts.opponent_item = {source: "SYNTHETIC", value: "Imaginary Item", evidence_ids: ["fixture-DIRECT"]}; }, /item absent/);
  check(o => { o.facts.opponent_revealed_moves = {source: "SYNTHETIC", value: ["Moonblast"], evidence_ids: ["fixture-DIRECT"]}; }, /move absent/);
  check(o => { o.facts.opponent_ability = {source: "SYNTHETIC", value: "Overgrow", evidence_ids: ["fixture-DIRECT"]}; }, /ability absent/);
  check(o => { o.facts.opponent_item = {source: "INFERRED", value: "Lucarionite", reason: "hypothesis"}; }, /mega stone wrong species/);
  check(o => { o.facts.opponent_revealed_moves = {source: "INFERRED", value: ["Moonblast"], reason: "hypothesis"}; }, /move absent/);
  check(o => { o.facts.opponent_ap = {source: "INFERRED", value: {hp: 0, atk: 33, def: 0, spa: 0, spd: 0, spe: 0}, reason: "hypothesis"}; }, /invalid Champions opponent AP/);
  check(o => { o.facts.opponent_form = {source: "SYNTHETIC", value: "Garchomp-Mega", evidence_ids: ["fixture-DIRECT"]}; o.facts.opponent_mega_state = {source: "SYNTHETIC", value: false, evidence_ids: ["fixture-DIRECT"]}; }, /Mega state conflicts/);
  check(o => { o.facts.active_opponent = {source: "SYNTHETIC", value: "metagross", evidence_ids: ["fixture-DIRECT"]}; }, /active opponent and form disagree/);
  check(o => { o.facts.opponent_selected = {source: "SYNTHETIC", value: ["venusaur"], evidence_ids: ["fixture-DIRECT"]}; }, /absent from known selected three/);
  check(o => { delete o.facts.internal_rng; }, /invalid partial observation schema/);
  assert.throws(() => restoreChecked(base, fingerprint(state(syntheticTruth(ctx,
    {id: "DIRECT", truth: "A"})))), /snapshot input integrity mismatch/);
});

test("different hidden truth fixtures yield the same public facts and same candidate set", () => {
  const ctx = context(), a = {id: "NOLEAK", truth: "A"}, b = {id: "NOLEAK", truth: "B"};
  const observedA = projectObservation(syntheticTruth(ctx, a), ctx, a);
  const observedB = projectObservation(syntheticTruth(ctx, b), ctx, b);
  assert.deepEqual(observedA, observedB);
  const candidatesA = makeCandidates(observedA, ctx).candidates;
  const candidatesB = makeCandidates(observedB, ctx).candidates;
  assert.deepEqual(candidatesA.map(c => [c.id, c.state_sha256]),
    candidatesB.map(c => [c.id, c.state_sha256]));
  assert.deepEqual(observedA.facts.opponent_item, {source: "UNKNOWN"});
  assert.deepEqual(observedA.facts.opponent_revealed_moves, {source: "UNKNOWN"});
});

test("illegal move and item cannot enter a candidate registered six", () => {
  const ctx = context();
  assert.throws(() => selectedTeams(ctx, "A", {move: "Moonblast"}),
    /candidate fixture not legal/);
  assert.throws(() => selectedTeams(ctx, "A", {item: "Imaginary Item"}),
    /candidate fixture not legal/);
  assert.equal(selectedTeams(ctx, "B")[1].length, 6);
});

test("candidate generation is isolated and an unknown speed relation cannot select a set", () => {
  const ctx = context();
  const obs = projectObservation(syntheticTruth(ctx, {id: "ISOLATED", truth: "A"}), ctx,
    {id: "ISOLATED", truth: "A"});
  const generated = makeCandidates(obs, ctx);
  const otherHash = fingerprint(state(generated.candidates[1].battle));
  generated.candidates[0].battle.p2.active[0].hp = 1;
  assert.equal(fingerprint(state(generated.candidates[1].battle)), otherHash);
  const event = applyEvent(obs, generated.candidates, {type: "action_order_seen", value: "p2_before_p1"}, ctx, 1);
  assert.equal(event.candidates.length, 4);
  assert.equal(obs.facts.speed_relation.source, "INFERRED");
  assert.deepEqual(obs.facts.opponent_ap, {source: "UNKNOWN"});
});

test("confirmed HP and field conditions cannot silently fit the initial candidate Battle", () => {
  const ctx = context();
  const entry = {id: "PUBLIC", truth: "A"};
  const base = projectObservation(syntheticTruth(ctx, entry), ctx, entry);
  const hp = structuredClone(base);
  hp.facts.opponent_hp_exact = {source: "SYNTHETIC", value: 1,
    evidence_ids: ["fixture-PUBLIC"]};
  const excluded = makeCandidates(hp, ctx);
  assert.equal(excluded.candidates.length, 0);
  assert.equal(excluded.rejected.length, 4);
  const wall = structuredClone(base);
  wall.facts.screens = {source: "SYNTHETIC", value: ["reflect"],
    evidence_ids: ["fixture-PUBLIC"]};
  const conditioned = makeCandidates(wall, ctx);
  assert.equal(conditioned.candidates.length, 4);
  assert.ok(conditioned.candidates.every(c => !c.battle_applicable));
});

test("case deletion, substitution and relabeling fail the fixed manifest", () => {
  assert.throws(() => assertManifest(manifest.cases.slice(1)), /coverage changed/);
  const altered = structuredClone(manifest.cases);
  altered[0].truth = "B";
  assert.throws(() => assertManifest(altered), /coverage changed/);
  const status = structuredClone(manifest.cases);
  status[15].expected_status = "SYNTHETIC_TEST_PASS";
  assert.throws(() => assertManifest(status), /coverage changed/);
});
