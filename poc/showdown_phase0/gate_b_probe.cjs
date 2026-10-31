"use strict";
// Gate B diagnostic only. All battles and observations here are synthetic.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {execFileSync} = require("node:child_process");
const {performance} = require("node:perf_hooks");
const {create, copy, serialize, restore, observed, own, opponents, pinned, loadError,
  firstDifferentPaths} = require("./worker.cjs");
const {fingerprint, stableStringify, normalizeReplayTimestamps,
  classifySerializedDivergence} = require("./state_compare.cjs");
const {allowedChampionsSets} = require("./wiki_damage_compare.cjs");
const {moveKey} = require("./champions_move_key.cjs");
const manifest = require("./gate_b_cases.json");
const SEED = [17, 23, 31, 47];
const GAP_FIELDS = ["opponent_unseen_moves", "opponent_remaining_pp",
  "opponent_unrevealed_ability", "opponent_unrevealed_item", "opponent_ability_points",
  "internal_rng_state", "unobserved_effect_history", "unresolved_action_queue"];
const ABILITY_JA = {roughskin: "さめはだ", torrent: "げきりゅう",
  disguise: "ばけのかわ", sandstream: "すなおこし", pressure: "プレッシャー",
  innerfocus: "せいしんりょく"};
const MASTER_FILES = ["Pokemon_Champions_MC_move_master.csv",
  "Pokemon_Champions_MC_usable_pokemon.csv",
  "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
  "Pokemon_Champions_MC_usable_held_items.csv"];
function masterHashes() {
  const dir = process.env.CHAMPIONS_MASTERS_DIR || path.join(__dirname, "masters");
  return Object.fromEntries(MASTER_FILES.map(name => [name,
    crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, name))).digest("hex")]));
}

function assertManifest(cases = manifest.cases) {
  const expected = ["normal_after_turn", "both_hp_damaged", "pp_consumed",
    "boost_changed", "status_applied", "item_consumed", "choice_lock",
    "choice_unlock_after_switch", "weather_and_wall_duration", "post_mega_history",
    "forced_switch_request", "one_pending_choice", "unknown_opponent_move_ability_item",
    "multiple_sets_same_observation"];
  const setups = ["normal", "both_hp", "pp", "boost", "status", "item", "choice",
    "unlock", "weather", "mega", "forced", "pending", "unknown_set", "multiple_sets"];
  if (cases.length !== expected.length || new Set(cases.map(c => c.id)).size !== expected.length ||
    cases.some((c, i) => c.mechanism !== expected[i] || c.setup !== setups[i] ||
      c.id !== `${i < 12 ? "B1" : "B2"}-${String(i + 1).padStart(2, "0")}` ||
      c.phase !== (i < 12 ? "B1" : "B2") ||
      c.expected_status !== (i < 12 ? "PASS_SYNTHETIC" : "INCONCLUSIVE")))
    throw Error("Gate B case coverage changed; review the fixed 14-case contract");
}
function assertPinnedMasterSets() {
  if (loadError) throw Error(loadError);
  const root = process.env.SHOWDOWN_ROOT;
  const sha = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {encoding: "utf8"}).trim();
  if (sha !== pinned) throw Error("pinned Showdown SHA mismatch: " + sha);
  const {Dex} = require(path.join(root, "dist", "sim", "index.js"));
  const dex = Dex.forFormat("gen9championsbssregmc");
  if (dex.currentMod !== "champions") throw Error("Champions mod inactive");
  const allowed = allowedChampionsSets(dex);
  for (const mon of [...own, ...opponents(0), ...opponents(1)]) {
    const species = dex.species.get(mon.species);
    const permitted = allowed.species.get(species.id);
    if (!permitted || mon.item && !allowed.availableItems.has(dex.items.get(mon.item).id))
      throw Error("unavailable Champions species or item: " + mon.species);
    const abilityId = dex.abilities.get(mon.ability).id;
    if (!ABILITY_JA[abilityId] || !permitted.abilities.has(ABILITY_JA[abilityId]) ||
        !Object.values(species.abilities).some(name => dex.abilities.get(name).id === abilityId))
      throw Error("unavailable Champions ability: " + mon.species + "/" + mon.ability);
    for (const name of mon.moves) {
      const id = moveKey(name);
      if (!id || !allowed.availableMoves.has(id) || !permitted.learned.has(id) ||
        !dex.moves.get(id).exists)
        throw Error("unlearnable Champions move: " + mon.species + "/" + name);
    }
  }
  return sha;
}
function state(b) { return normalizeReplayTimestamps(b.toJSON()); }
function same(a, b) { return stableStringify(state(a)) === stableStringify(state(b)); }
function ms(start) { return performance.now() - start; }
function setup(kind) {
  const b = create(SEED, 0, kind !== "pending");
  const p1 = b.p1.active[0], p2 = b.p2.active[0];
  const marker = {source: "SHOWDOWN_EXECUTED_TURN"};
  switch (kind) {
    case "normal":
      b.makeChoices("move 1", "move 1");
      break;
    case "both_hp":
      if (!(p1.hp < p1.maxhp && p2.hp < p2.maxhp))
        throw Error("both active HP did not decrease in executed turn");
      break;
    case "pp":
      if (!(p1.moveSlots[0].pp < p1.moveSlots[0].maxpp))
        throw Error("PP not consumed in executed turn");
      break;
    case "boost":
      b.p1.active[0].boosts.atk = 1;
      marker.source = "SYNTHETIC_STATE_INJECTION";
      break;
    case "status":
      b.p1.active[0].status = "par";
      marker.source = "SYNTHETIC_STATE_INJECTION";
      break;
    case "item":
      // Use Showdown's item consumption path, not a bare item deletion.
      p2.hp = Math.max(1, Math.floor(p2.maxhp / 4));
      marker.source = "SYNTHETIC_HP_SETUP_THEN_ENGINE_ITEM_EVENT";
      if (!p2.eatItem() || p2.item || !p2.lastItem)
        throw Error("engine did not consume the Sitrus Berry with history");
      break;
    case "choice":
      if (p1.item !== "choicescarf" || !p1.volatiles.choicelock?.move)
        throw Error("executed turn did not set choice lock");
      break;
    case "unlock":
      b.makeChoices("switch 2", "move 1");
      if (b.requestState !== "move") throw Error("no move request after switch out");
      b.makeChoices("switch 2", "move 1");
      if (b.p1.active[0]?.species.id !== "garchomp" || b.p1.active[0].volatiles.choicelock)
        throw Error("choice lock did not clear after re-entry");
      break;
    case "weather":
      b.field.setWeather("raindance", p1);
      b.p1.addSideCondition("reflect", p1);
      marker.source = "SYNTHETIC_ENGINE_EFFECT_SETUP";
      if (!(b.field.weatherState.duration > 0 && b.p1.sideConditions.reflect?.duration > 0))
        throw Error("weather or wall duration absent");
      break;
    case "mega":
      b.makeChoices("switch 2", "switch 3");
      if (b.p2.active[0]?.species.id !== "lucario" || b.requestState !== "move")
        throw Error("Lucario switch did not resolve");
      b.makeChoices("move 4", "move 1 mega");
      if (!b.p2.active[0]?.species.isMega ||
          !b.log.some(line => line.startsWith("|-mega|p2a: Lucario|")))
        throw Error("post-mega form or history missing");
      break;
    case "forced":
      p1.switchFlag = true;
      b.makeRequest("switch");
      marker.source = "SYNTHETIC_FORCED_SWITCH_REQUEST";
      if (b.requestState !== "switch") throw Error("forced switch request absent");
      break;
    case "pending":
      if (!b.choose("p1", "move 1") || b.p1.choice.actions?.length !== 1)
        throw Error("pending action not accepted");
      break;
    default: throw Error("unknown setup: " + kind);
  }
  return {battle: b, evidence: marker};
}
function perform(b, side, action) {
  if (!b.choose(side, action)) throw Error(`illegal ${side} action ${action}`);
}
function resolveFrom(snapshot, action, timing = null) {
  let start = performance.now();
  const b = copy(snapshot);
  if (timing) timing.clone = ms(start);
  if (b.requestState === "switch") {
    start = performance.now();
    perform(b, "p1", action);
    if (timing) timing.choice_and_resolution = ms(start);
    if (b.requestState === "switch" && b.p2.activeRequest?.forceSwitch?.some(Boolean))
      perform(b, "p2", "switch 2");
  } else if (b.p1.choice.actions?.length) {
    start = performance.now();
    perform(b, "p2", action);
    if (timing) timing.choice_and_resolution = ms(start);
  } else {
    start = performance.now();
    perform(b, "p1", action);
    if (timing) timing.choice_input = ms(start);
    start = performance.now();
    perform(b, "p2", "move 1");
    if (timing) timing.resolution = ms(start);
  }
  return b;
}
function actionsFor(b) {
  if (b.requestState === "switch") return ["switch 2", "switch 3"];
  if (b.p1.choice.actions?.length) return ["move 1", "move 2"];
  return ["move 1", "switch 2"];
}
function actionId(b, action) {
  const [verb, slot] = action.split(" ");
  if (verb === "switch") return b.p1.choice.actions?.length
    ? b.p2.pokemon[Number(slot) - 1]?.species.id : b.p1.pokemon[Number(slot) - 1]?.species.id;
  const pokemon = b.p1.choice.actions?.length ? b.p2.active[0] : b.p1.active[0];
  return pokemon?.moveSlots[Number(slot) - 1]?.id || null;
}
function diff(a, b) {
  const left = state(a), right = state(b);
  return {first_difference_field: firstDifferentPaths(left, right)[0] || null,
    comparison: classifySerializedDivergence(left, right)};
}
function runB1(entry) {
  const result = {case_id: entry.id, phase: "B1", mechanism: entry.mechanism,
    evidence_class: "SYNTHETIC", seed: SEED, pinned_showdown_sha: pinned,
    status: "UNSUPPORTED", unsupported_fields: []};
  let snapshot;
  try {
    const {battle, evidence} = setup(entry.setup);
    snapshot = battle;
    result.setup_provenance = evidence.source;
    result.input_state_sha256 = fingerprint(state(snapshot));
    result.restore_source_state = snapshot.toJSON();
    let start = performance.now();
    const wire = serialize(snapshot);
    const serialize_ms = ms(start);
    start = performance.now();
    const restored = restore(wire);
    result.timing_ms = {serialize: serialize_ms, restore: ms(start)};
    result.restored_state = restored.toJSON();
    const restoredDiff = diff(snapshot, restored);
    result.first_difference_field = restoredDiff.first_difference_field;
    if (!same(snapshot, restored)) {
      result.status = restoredDiff.comparison.kind === "LOG_ONLY" ?
        "LOG_ONLY_MISMATCH" : "STATE_OR_QUEUE_MISMATCH";
      result.reason = "serialized state changed on restore";
      return result;
    }
    const [aAction, bAction] = actionsFor(restored);
    result.continuation_actions = [aAction, bAction];
    result.action_ids = [actionId(restored, aAction), actionId(restored, bAction)];
    const frozen = fingerprint(state(snapshot)), restoredFrozen = fingerprint(state(restored));
    const branchB = copy(restored), branchBBefore = fingerprint(state(branchB));
    const branchTiming = {};
    const a = resolveFrom(restored, aAction, branchTiming);
    Object.assign(result.timing_ms, branchTiming);
    if (fingerprint(state(branchB)) !== branchBBefore || fingerprint(state(restored)) !== restoredFrozen ||
      fingerprint(state(snapshot)) !== frozen)
      throw Error("branch A changed source or branch B");
    let clock = performance.now();
    const b = resolveFrom(branchB, bAction);
    result.timing_ms.branch_b = ms(clock);
    clock = performance.now();
    const repeat = resolveFrom(restored, aAction);
    result.timing_ms.repeat_a = ms(clock);
    result.continuation_result = {branch_a_sha256: fingerprint(state(a)),
      branch_b_sha256: fingerprint(state(b)), repeat_a_sha256: fingerprint(state(repeat)),
      branch_difference: diff(a, b), replay_difference: diff(a, repeat)};
    result.branch_independence = {source_unchanged: fingerprint(state(snapshot)) === frozen,
      restored_unchanged: fingerprint(state(restored)) === restoredFrozen,
      branch_b_unchanged_before_execution: true,
      repeat_reproduced: same(a, repeat), distinct_actions: aAction !== bAction,
      distinct_results: !same(a, b)};
    if (!result.branch_independence.source_unchanged || !result.branch_independence.restored_unchanged ||
      !result.branch_independence.repeat_reproduced) {
      const replay = diff(a, repeat);
      result.first_difference_field = replay.first_difference_field;
      result.status = replay.comparison.kind === "LOG_ONLY" ?
        "LOG_ONLY_MISMATCH" : "STATE_OR_QUEUE_MISMATCH";
      result.reason = "branch replay or source independence failed";
    } else if (!result.branch_independence.distinct_results ||
        result.action_ids[0] === result.action_ids[1]) {
      result.status = "INCONCLUSIVE";
      result.reason = "alternate action gave no distinct state or action ID";
    } else {
      result.status = "PASS_SYNTHETIC";
      result.reason = "full state, replay, and independent legal branches matched";
    }
  } catch (e) {
    result.reason = e.message;
    result.unsupported_fields = [entry.mechanism];
    result.first_difference_field ||= null;
    result.branch_independence ||= null;
    result.continuation_result ||= null;
    if (snapshot) result.input_state_sha256 ||= fingerprint(state(snapshot));
  }
  return result;
}
function runB2(entry) {
  const a = create(SEED, 0, false), b = create(SEED, 1, false);
  const oa = observed(a), ob = observed(b);
  const sameView = stableStringify(oa) === stableStringify(ob);
  const unknown = Object.fromEntries(GAP_FIELDS.map(field => [field, "UNKNOWN"]));
  const differentModels = fingerprint(state(a)) !== fingerprint(state(b));
  let hypothesisCheck = null;
  if (entry.setup === "multiple_sets" && sameView && differentModels) {
    const frozenA = fingerprint(state(a)), frozenB = fingerprint(state(b));
    const moveA = a.p2.active[0].moveSlots[0].id;
    const moveB = b.p2.active[0].moveSlots[0].id;
    const resolvedA = copy(a), resolvedB = copy(b);
    resolvedA.makeChoices("move 1", "move 1");
    resolvedB.makeChoices("move 1", "move 1");
    hypothesisCheck = {opponent_move_ids: [moveA, moveB],
      output_sha256: [fingerprint(state(resolvedA)), fingerprint(state(resolvedB))],
      distinct_outputs: !same(resolvedA, resolvedB),
      source_states_unchanged: fingerprint(state(a)) === frozenA && fingerprint(state(b)) === frozenB};
  }
  const hypothesisValid = entry.setup !== "multiple_sets" ||
    hypothesisCheck?.opponent_move_ids[0] !== hypothesisCheck?.opponent_move_ids[1] &&
    hypothesisCheck?.distinct_outputs && hypothesisCheck?.source_states_unchanged;
  const valid = sameView && differentModels && hypothesisValid;
  return {case_id: entry.id, phase: "B2", mechanism: entry.mechanism,
    evidence_class: "SYNTHETIC_PARTIAL_OBSERVATION", seed: SEED,
    pinned_showdown_sha: pinned, input_state_sha256: fingerprint(oa),
    restore_source_state: {observed: oa, unknown}, restored_state: null,
    first_difference_field: null, continuation_actions: hypothesisCheck ? ["move 1", "move 1"] : [],
    continuation_result: hypothesisCheck, branch_independence: hypothesisCheck,
    unsupported_fields: GAP_FIELDS,
    hypothesis_state_sha256: [fingerprint(state(a)), fingerprint(state(b))],
    same_observation: sameView, multiple_consistent_models: differentModels,
    status: valid ? "INCONCLUSIVE" : "STATE_OR_QUEUE_MISMATCH",
    reason: valid
      ? "observed fields admit distinct hidden sets; no real-game state or RNG to restore"
      : "synthetic hypothesis precondition failed"};
}
function summarize(results) {
  const statuses = ["PASS_SYNTHETIC", "STATE_OR_QUEUE_MISMATCH", "LOG_ONLY_MISMATCH",
    "INCONCLUSIVE", "UNSUPPORTED"];
  return Object.fromEntries(["B1", "B2"].map(phase => [phase,
    Object.fromEntries(statuses.map(status =>
      [status, results.filter(r => r.phase === phase && r.status === status).length]))]));
}
function stageLatencies(results) {
  const stages = ["serialize", "restore", "clone", "choice_input", "resolution",
    "choice_and_resolution", "branch_b", "repeat_a"];
  const percentile = (sorted, fraction) => sorted[Math.ceil(sorted.length * fraction) - 1] ?? null;
  return Object.fromEntries(stages.map(stage => {
    const numbers = results.filter(r => r.phase === "B1" && r.status === "PASS_SYNTHETIC")
      .map(r => r.timing_ms[stage]).filter(Number.isFinite).sort((a, b) => a - b);
    return [stage, {samples: numbers.length, p50: percentile(numbers, .5),
      p95: percentile(numbers, .95), p99: percentile(numbers, .99)}];
  }));
}
function run() {
  assertManifest();
  assertPinnedMasterSets();
  // Uniform warmup for all timing stages; excluded from case and timing counts.
  for (let i = 0; i < 10; i++) copy(create(SEED));
  const results = manifest.cases.map(entry => entry.phase === "B1" ? runB1(entry) : runB2(entry));
  const unique = new Set(results.map(r => r.input_state_sha256).filter(Boolean));
  return {schema: "phase0-gate-b-diagnostics-v1", pinned_showdown_sha: pinned,
    champions_master_sha256: masterHashes(),
    format: "gen9championsbssregmc", gate_A: "UNKNOWN", gate_B: "UNSUPPORTED",
    adoption: "NO_GO", case_count: results.length, unique_input_state_count: unique.size,
    mechanism_count: new Set(results.map(r => r.mechanism)).size, repetitions: 0,
    representative_100_cases_met: false, warmup_iterations: 10,
    stage_latency_ms: stageLatencies(results), counts: summarize(results), results};
}
if (require.main === module) {
  try {process.stdout.write(JSON.stringify(run()) + "\n");}
  catch (e) {process.stdout.write(JSON.stringify({status: "NOT_RUN", reason: e.message}) + "\n"); process.exitCode = 2;}
}
module.exports = {run, assertManifest, summarize, runB1, runB2, GAP_FIELDS};
