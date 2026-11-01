"use strict";
// Phase 0 only: 127 independently defined synthetic *state conditions*, not game observations.
// Repetitions, RNG-seed changes, selected-order-only changes and exact-HP-only
// differences are excluded from the tactical diversity counter.
const {performance} = require("node:perf_hooks");
const {context, validateRegistration, makeSelectedBattle} = require("./six_team_selection_probe.cjs");
const {state, same, diff, restoreChecked, resolveFrom, actionId, masterHashes} =
  require("./gate_b_probe.cjs");
const {serialize, pinned} = require("./worker.cjs");
const {fingerprint, stableStringify} = require("./state_compare.cjs");
const manifest = require("./representative_states_cases.json");

const FORMAT = "gen9championsbssregmc";
const MODES = ["baseline", "both_hp_damage", "pp_consumption", "paralysis",
  "boosts", "rain_and_reflect", "sitrus_consumption", "post_mega"];
const ID_COUNT = 127;

function caseContract() {
  const expected = [];
  for (const mechanism of MODES.slice(0, -1)) {
    for (let i = 0; i < 18; i++) {
      expected.push({id: "REP-" + String(expected.length + 1).padStart(3, "0"),
        mechanism, pair_id: "PAIR-" + String(i + 1).padStart(2, "0"),
        p1_active_slot: i % 6 + 1,
        p2_active_slot: (i % 6 + Math.floor(i / 6) * 2) % 6 + 1,
        expected_status: "PASS_SYNTHETIC"});
    }
  }
  expected.push({id: "REP-127", mechanism: "post_mega",
    pair_id: "MEGA-GARCHOMP-GYARADOS", p1_active_slot: 1, p2_active_slot: 2,
    expected_status: "PASS_SYNTHETIC"});
  return expected;
}
function assertManifest(entries = manifest.cases) {
  if (manifest.schema !== "phase0-representative-states-cases-v1" ||
      entries.length !== ID_COUNT ||
      stableStringify(entries) !== stableStringify(caseContract()))
    throw Error("representative state identity, mechanism, or coverage changed");
}
function selectionFor(target, thirdOverride = null) {
  if (!Number.isInteger(target) || target < 1 || target > 6)
    throw Error("invalid target slot");
  const first = target % 6 + 1;
  let third = thirdOverride || (target + 1) % 6 + 1;
  if (third === first || third === target) throw Error("selection repeats slot");
  return {slots: [first, target, third], lead_slot: first};
}
function syntheticSetup(entry, ctx) {
  const registered = ctx.registered.map(team => structuredClone(team));
  const choices = [selectionFor(entry.p1_active_slot),
    selectionFor(entry.p2_active_slot,
      entry.mechanism === "post_mega" ? 6 : null)];
  const origins = ["M_C_EXECUTED_SELECTION", "EXECUTED_SWITCH_TURN"];
  if (entry.mechanism === "sitrus_consumption")
    registered[1][entry.p2_active_slot - 1].item = "Sitrus Berry";
  if (entry.mechanism === "post_mega") {
    if (registered[0][entry.p1_active_slot - 1].species !== "Garchomp")
      throw Error("mega test must select Garchomp");
    registered[0][entry.p1_active_slot - 1].item = "Garchompite";
  }
  const registration = registered.map(team => validateRegistration(team, ctx));
  if (registration.some(r => !r.legal))
    throw Error("registered six not jointly validated: " + JSON.stringify(registration));
  const {battle: b, selected, preview} = makeSelectedBattle(registered, choices);
  if (preview.registered_sizes.some(n => n !== 6) ||
      selected.some(s => !s.passed) || b.requestState !== "move")
    throw Error("registered six / selected three did not match");
  b.makeChoices("switch 2", "switch 2");
  if (b.requestState !== "move" || b.turn < 2)
    throw Error("initial synthetic switch turn did not complete");
  const a = b.p1.active[0], d = b.p2.active[0];
  if (a.species.id !== ctx.dex.species.get(registered[0][entry.p1_active_slot - 1].species).id ||
      d.species.id !== ctx.dex.species.get(registered[1][entry.p2_active_slot - 1].species).id)
    throw Error("selected lead swap does not match requested active matchup");
  switch (entry.mechanism) {
    case "baseline": break;
    case "both_hp_damage":
      a.hp = Math.max(1, Math.floor(a.maxhp * 0.70));
      d.hp = Math.max(1, Math.floor(d.maxhp * 0.62));
      origins.push("SYNTHETIC_HP_INJECTION");
      break;
    case "pp_consumption":
      if (a.moveSlots[0].pp < 2 || d.moveSlots[0].pp < 2)
        throw Error("not enough PP for synthetic consumed PP");
      a.moveSlots[0].pp--; d.moveSlots[0].pp--;
      origins.push("SYNTHETIC_PP_INJECTION");
      break;
    case "paralysis":
      a.status = "par";
      origins.push("SYNTHETIC_STATUS_INJECTION");
      break;
    case "boosts":
      a.boosts.atk = 1; d.boosts.def = -1;
      origins.push("SYNTHETIC_BOOST_INJECTION");
      break;
    case "rain_and_reflect":
      if (!b.field.setWeather("raindance", a) ||
          !b.p1.addSideCondition("reflect", a) ||
          !(b.field.weatherState.duration > 0) ||
          !(b.p1.sideConditions.reflect?.duration > 0))
        throw Error("Showdown could not set rain and Reflect");
      origins.push("SYNTHETIC_ENGINE_EFFECT_SETUP");
      break;
    case "sitrus_consumption":
      d.hp = Math.max(1, Math.floor(d.maxhp / 4));
      if (!d.eatItem() || d.item || d.lastItem !== "sitrusberry")
        throw Error("held Sitrus Berry was not consumed by Showdown");
      origins.push("SYNTHETIC_HP_SETUP_THEN_ENGINE_ITEM_EVENT");
      break;
    case "post_mega":
      b.makeChoices("move 1 mega", "switch 3");
      if (!b.p1.active[0]?.species.isMega ||
          !b.log.some(line => line.startsWith("|-mega|p1a:")))
        throw Error("engine Mega event did not execute");
      if (b.requestState !== "move") throw Error("post-Mega action request absent");
      origins.push("EXECUTED_MEGA_TURN");
      break;
    default: throw Error("unknown representative mechanism");
  }
  return {battle: b, origins, choices, registration,
    current_species: b.sides.map(side => side.active[0]?.species.id)};
}
function tacticallyRelevant(b) {
  const mon = p => ({
    species: p.species.id, current_types: [...p.types],
    mega: !!p.species.isMega, hp_band: p.hp === p.maxhp ? "FULL" :
      p.hp === 0 ? "FAINTED" : "DAMAGED",
    move_pp_spent: p.moveSlots.map(m => [m.id, m.maxpp - m.pp]),
    status: p.status, boosts: {...p.boosts},
    item: p.item, last_item: p.lastItem || "",
    choice_lock: p.volatiles.choicelock?.move || "",
  });
  return {active: b.sides.map(s => mon(s.active[0])),
    weather: b.field.weather,
    p1_reflect: Boolean(b.p1.sideConditions.reflect),
    p2_reflect: Boolean(b.p2.sideConditions.reflect)};
}
function timingStats(values) {
  const numbers = values.filter(Number.isFinite).sort((a, b) => a - b);
  const at = q => numbers[Math.ceil(q * numbers.length) - 1] ?? null;
  return {samples: numbers.length, p50: at(0.5), p95: at(0.95),
    p99: at(0.99), max: numbers.at(-1) ?? null};
}
function runCase(entry, ctx) {
  const result = {case_id: entry.id, mechanism: entry.mechanism,
    pair_id: entry.pair_id, evidence_class: "SYNTHETIC_ONLY",
    status: "UNSUPPORTED", reason: null};
  const clock = {};
  let snapshot = null;
  try {
    let t = performance.now();
    const prepared = syntheticSetup(entry, ctx);
    clock.prepare_ms = performance.now() - t;
    snapshot = prepared.battle;
    result.setup_provenance = prepared.origins;
    result.selected = prepared.choices;
    result.current_species = prepared.current_species;
    result.state_sha256 = fingerprint(state(snapshot));
    result.tactical_sha256 = fingerprint(tacticallyRelevant(snapshot));
    result.tactical_state = tacticallyRelevant(snapshot);
    t = performance.now();
    const wire = serialize(snapshot);
    clock.serialize_ms = performance.now() - t;
    t = performance.now();
    const restored = restoreChecked(wire, result.state_sha256);
    clock.restore_ms = performance.now() - t;
    if (!same(snapshot, restored))
      throw Error("serialized complete state changed after restore");
    const before = fingerprint(state(snapshot));
    const restoredBefore = fingerprint(state(restored));
    const aAction = "move 1", bAction = "switch 1";
    const ids = [actionId(restored, aAction), actionId(restored, bAction)];
    if (!ids[0] || !ids[1] || ids[0] === ids[1])
      throw Error("branch actions do not identify different legal actions");
    t = performance.now();
    const a = resolveFrom(restored, aAction);
    clock.branch_ms = performance.now() - t;
    t = performance.now();
    const b = resolveFrom(restored, bAction);
    clock.alternative_ms = performance.now() - t;
    t = performance.now();
    const replay = resolveFrom(restored, aAction);
    clock.replay_ms = performance.now() - t;
    const retained = fingerprint(state(snapshot)) === before &&
      fingerprint(state(restored)) === restoredBefore;
    const replayed = same(a, replay), branched = !same(a, b);
    if (!retained || !replayed || !branched)
      throw Error("restored branch independence, replay or alternate result mismatch");
    result.branch = {actions: [aAction, bAction], action_ids: ids,
      a_sha256: fingerprint(state(a)), b_sha256: fingerprint(state(b)),
      replay_a_sha256: fingerprint(state(replay)), source_unchanged: retained,
      repeated_action_identical: replayed, distinct_outcome: branched};
    result.status = "PASS_SYNTHETIC";
    result.reason = "complete synthetic M-C selected state restored and branched";
  } catch (error) {
    result.reason = error.message;
    result.first_difference_field = snapshot ? "UNRESOLVED_CHECK" : "PREPARATION";
  }
  result.timings_ms = clock;
  return result;
}
function validateReport(report) {
  if (report.case_count !== ID_COUNT || report.results.length !== ID_COUNT ||
      report.counts.PASS_SYNTHETIC !== ID_COUNT ||
      report.counts.UNSUPPORTED !== 0 ||
      report.distinct_complete_states !== ID_COUNT ||
      report.distinct_tactical_states !== ID_COUNT ||
      report.distinct_matchups !== 18 ||
      report.distinct_mechanisms !== MODES.length ||
      report.synthetic_mechanism_coverage_met !== true ||
      report.representative_100_synthetic_states_met !== true ||
      report.results.some((r, i) => r.case_id !== manifest.cases[i].id ||
        r.status !== manifest.cases[i].expected_status ||
        r.branch?.repeated_action_identical !== true ||
        r.branch?.source_unchanged !== true ||
        r.branch?.distinct_outcome !== true))
    throw Error("representative coverage, duplicate state or restoration regression: " +
      JSON.stringify({count: report.case_count, full: report.distinct_complete_states,
        tactical: report.distinct_tactical_states, matchups: report.distinct_matchups,
        mechanisms: report.distinct_mechanisms, counts: report.counts,
        failed_cases: report.results.filter(r => r.status !== "PASS_SYNTHETIC")
          .map(r => ({id: r.case_id, kind: r.mechanism, reason: r.reason})).slice(0, 20)}));
  return true;
}
function run() {
  assertManifest();
  const ctx = context();
  // Warmups never count toward 127 defined input states or any timing sample.
  for (let i = 0; i < 6; i++) syntheticSetup(manifest.cases[0], ctx);
  const initialMemory = process.memoryUsage();
  const uptimeBeforeCases = process.uptime() * 1000;
  const results = manifest.cases.map(c => runCase(c, ctx));
  const full = new Set(results.map(r => r.state_sha256).filter(Boolean));
  const tact = new Set(results.map(r => r.tactical_sha256).filter(Boolean));
  const matchups = new Set(results.filter(r => r.mechanism !== "post_mega")
    .map(r => r.current_species?.join("/")).filter(Boolean));
  const timeKeys = ["prepare_ms", "serialize_ms", "restore_ms", "branch_ms",
    "alternative_ms", "replay_ms"];
  const afterMemory = process.memoryUsage();
  const report = {schema: "phase0-representative-synthetic-states-v1",
    pinned_showdown_sha: pinned, champions_master_sha256: masterHashes(),
    format: FORMAT, regulation: "M-C", evidence_class: "SYNTHETIC_ONLY",
    gate_A: "UNKNOWN", gate_B: "UNSUPPORTED", adoption: "NO_GO",
    case_count: results.length, distinct_complete_states: full.size,
    distinct_tactical_states: tact.size, distinct_matchups: matchups.size,
    distinct_mechanisms: new Set(results.map(r => r.mechanism)).size,
    distinct_registered_team_pairs: new Set(results.map(r =>
      r.mechanism === "post_mega" ? "MEGA" :
      r.mechanism === "sitrus_consumption" ? "SITRUS" : "BASE")).size,
    repetitions: 0, warmup_iterations: 6,
    counts: Object.fromEntries(["PASS_SYNTHETIC", "UNSUPPORTED"].map(s =>
      [s, results.filter(r => r.status === s).length])),
    timing_ms: Object.fromEntries(timeKeys.map(key =>
      [key, timingStats(results.map(r => r.timings_ms[key]))])),
    memory_bytes: {initial_rss: initialMemory.rss, final_rss: afterMemory.rss,
      initial_heap_used: initialMemory.heapUsed, final_heap_used: afterMemory.heapUsed,
      final_heap_total: afterMemory.heapTotal},
    node_process_uptime_before_case_ms: uptimeBeforeCases,
    // No Windows display E2E or native game measurement was performed.
    native_game_verification: false, windows_e2e_verified: false,
    synthetic_mechanism_coverage_met: MODES.every(m => results.some(r =>
      r.mechanism === m && r.status === "PASS_SYNTHETIC")),
    representative_100_synthetic_states_met: full.size >= 100 && tact.size >= 100 &&
      matchups.size >= 18 && results.filter(r => r.status === "PASS_SYNTHETIC").length >= 100,
    results};
  validateReport(report);
  return report;
}
if (require.main === module) {
  try {process.stdout.write(JSON.stringify(run()) + "\n");}
  catch (error) {process.stdout.write(JSON.stringify({status: "NOT_RUN", reason: error.message}) + "\n"); process.exitCode = 2;}
}
module.exports = {run, runCase, assertManifest, validateReport, caseContract,
  selectionFor, syntheticSetup, tacticallyRelevant, timingStats};
