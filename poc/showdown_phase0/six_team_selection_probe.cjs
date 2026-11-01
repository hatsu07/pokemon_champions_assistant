"use strict";
// Phase 0 only: complete synthetic registered teams, not observed game state.
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const {Battle, Dex, TeamValidator} = require(path.join(process.env.SHOWDOWN_ROOT || "/missing",
  "dist", "sim", "index.js"));
const {allowedChampionsSets, validateChampionsScenario, validateChampionsRegisteredTeam,
  teamsForScenario, SCENARIOS} = require("./wiki_damage_compare.cjs");
const {serialize, pinned} = require("./worker.cjs");
const {state, same, diff, restoreChecked, resolveFrom, actionId,
  masterHashes} = require("./gate_b_probe.cjs");
const {fingerprint, stableStringify} = require("./state_compare.cjs");
const {moveKey} = require("./champions_move_key.cjs");
const manifest = require("./six_team_selection_cases.json");
const FORMAT = "gen9championsbssregmc";
const SEED = [41, 43, 47, 53];
const POSITIVE = ["first_three", "noncontiguous_three", "changed_lead_and_order",
  "post_selection_switch"];
const REG_NEGATIVE = ["five", "seven", "duplicate_species", "regional_duplicate",
  "duplicate_item", "unavailable_species", "mega_form", "unknown_move",
  "unlearnable_move", "duplicate_move", "invalid_ability", "invalid_item",
  "wrong_mega", "invalid_ap", "invalid_nature", "invalid_slots"];
const SELECTION_NEGATIVE = ["unregistered_selection_slot", "duplicate_selected_slot",
  "only_two_selected", "four_selected", "lead_order_mismatch"];
const DEFAULT_SELECTION = {slots: [1, 2, 3], lead_slot: 1};

function assertManifest(cases = manifest.cases) {
  if (cases.length !== 25 || new Set(cases.map(c => c.id)).size !== 25)
    throw Error("six-team case coverage changed");
  for (const [i, c] of cases.entries()) {
    const kind = i < 4 ? "positive" : i < 20 ? "negative_registration" : "negative_selection";
    const id = `${i < 4 ? "REG" : "NEG"}-${String(i < 4 ? i + 1 : i - 3).padStart(3, "0")}`;
    const mechanism = i < 4 ? POSITIVE[i] : i < 20 ? REG_NEGATIVE[i - 4] : SELECTION_NEGATIVE[i - 20];
    const expected = i < 4 ? "PASS_SYNTHETIC" : i < 20 ?
      "REJECTED_INVALID_REGISTRATION" : "REJECTED_INVALID_SELECTION";
    if (c.kind !== kind || c.id !== id || c.expected_status !== expected ||
        (i < 20 ? (c.mutation || c.mechanism) : c.mechanism) !== mechanism)
      throw Error("six-team case identity or expectation changed: " + id);
  }
}
function context() {
  const root = process.env.SHOWDOWN_ROOT;
  if (!root) throw Error("SHOWDOWN_ROOT required");
  const sha = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {encoding: "utf8"}).trim();
  if (sha !== pinned) throw Error("pinned Showdown SHA mismatch: " + sha);
  const dex = Dex.forFormat(FORMAT);
  if (dex.currentMod !== "champions") throw Error("Champions mod inactive");
  const allowed = allowedChampionsSets(dex);
  const source = SCENARIOS["WIKI-EARTHQUAKE-GARCHOMP-METAGROSS"];
  // Existing source-only scenario validation supplies the two registered sixes.
  validateChampionsScenario(source, dex, allowed);
  const [p1, p2] = teamsForScenario(source);
  const validator = TeamValidator.get(FORMAT);
  return {dex, allowed, validator, registered: [p1, p2]};
}
function normalizedTeam(team, dex) {
  return structuredClone(team).map(mon => ({...mon, moves: Array.isArray(mon.moves) ?
    mon.moves.map(name => {
      const key = moveKey(name);
      return key ? dex.moves.get(key).name : name;
    }) : mon.moves}));
}
function validateRegistration(team, ctx) {
  const result = {champions_master: {status: "REJECTED", reason: null},
    team_validator: {status: "REJECTED", reason: null}};
  try {
    validateChampionsRegisteredTeam(structuredClone(team), ctx.dex, ctx.allowed);
    result.champions_master = {status: "LEGAL", reason: null};
  } catch (error) { result.champions_master.reason = error.message; }
  try {
    const problems = ctx.validator.validateTeam(normalizedTeam(team, ctx.dex));
    result.team_validator = problems?.length ?
      {status: "REJECTED", reason: problems.join(" | ")} : {status: "LEGAL", reason: null};
  } catch (error) { result.team_validator.reason = error.message; }
  result.agree = result.champions_master.status === result.team_validator.status;
  result.legal = result.champions_master.status === "LEGAL" && result.team_validator.status === "LEGAL";
  return result;
}
function mutate(team, kind, ctx) {
  const t = structuredClone(team);
  const set = (species, ability, move = "Thunderbolt") =>
    ({species, ability, item: "", level: 50, nature: "Serious",
      evs: {hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 2}, moves: [move]});
  switch (kind) {
    case "five": t.pop(); break;
    case "seven": t.push(structuredClone(t[0])); break;
    case "duplicate_species": t[1] = structuredClone(t[0]); break;
    case "regional_duplicate":
      if (ctx.allowed.species.get("raichu")?.nationalNo !==
          ctx.allowed.species.get("raichualola")?.nationalNo)
        throw Error("regional National number not mapped in Champions master");
      t[0] = set("Raichu", "Static");
      t[1] = set("Raichu-Alola", "Surge Surfer");
      for (const slot of [0, 1]) {
        const separate = structuredClone(team);
        separate[slot] = structuredClone(t[slot]);
        if (!validateRegistration(separate, ctx).legal)
          throw Error("regional variant is not independently legal");
      }
      break;
    case "duplicate_item": t[0].item = "Leftovers"; t[1].item = "Leftovers"; break;
    case "unavailable_species": t[0].species = "Mewtwo"; break;
    case "mega_form": t[0].species = "Garchomp-Mega"; break;
    case "unknown_move": t[0].moves = ["Unknown Imaginary Move"]; break;
    case "unlearnable_move":
      if (ctx.allowed.species.get("garchomp")?.learned.has("moonblast"))
        throw Error("Moonblast is not an unlearnable control");
      t[0].moves = ["Moonblast"];
      break;
    case "duplicate_move": t[0].moves = ["Earthquake", "Earthquake"]; break;
    case "invalid_ability": t[0].ability = "Overgrow"; break;
    case "invalid_item": t[0].item = "Unknown Imaginary Item"; break;
    case "wrong_mega": t[0].item = "Lucarionite"; break;
    case "invalid_ap": t[0].evs.atk = 33; break;
    case "invalid_nature": t[0].nature = "Unknown Imaginary Nature"; break;
    case "invalid_slots": t[0].moves = []; break;
    default: throw Error("unknown registration mutation: " + kind);
  }
  return t;
}
function checkSelection(spec, registered) {
  if (!spec || !Array.isArray(spec.slots) || spec.slots.length !== 3)
    throw Error("SELECTION_GUARD: exactly three slots required");
  if (new Set(spec.slots).size !== 3 ||
      spec.slots.some(n => !Number.isInteger(n) || n < 1 || n > registered.length))
    throw Error("SELECTION_GUARD: slot unregistered or repeated");
  if (!Number.isInteger(spec.lead_slot) || spec.lead_slot !== spec.slots[0])
    throw Error("SELECTION_GUARD: lead must be the first selected slot");
  return "team " + spec.slots.join("");
}
function selectedData(b, registered, selection) {
  const sides = [b.p1, b.p2];
  return sides.map((side, i) => {
    const expected = selection[i].slots.map(slot => b.dex.species.get(registered[i][slot - 1].species).id);
    const actual = side.pokemon.map(mon => mon.species.id);
    const notSelected = registered[i].filter((_, index) => !selection[i].slots.includes(index + 1))
      .map(mon => b.dex.species.get(mon.species).id);
    const active = side.active[0]?.species.id;
    const passed = side.pokemon.length === 3 && stableStringify(actual) === stableStringify(expected) &&
      new Set(actual).size === 3 && active === expected[0] &&
      side.pokemon.every((mon, index) => mon.position === index) &&
      notSelected.every(id => !actual.includes(id));
    return {expected, actual, lead: active, bench: actual.slice(1),
      unselected: notSelected, passed};
  });
}
function makeSelectedBattle(registered, selection) {
  const commands = selection.map((spec, i) => checkSelection(spec, registered[i]));
  const b = new Battle({formatid: FORMAT, seed: SEED, strictChoices: true,
    p1: {name: "Synthetic six P1", team: normalizedTeam(registered[0], Dex.forFormat(FORMAT))},
    p2: {name: "Synthetic six P2", team: normalizedTeam(registered[1], Dex.forFormat(FORMAT))}});
  const preview = {request_state: b.requestState, picked_team_size: b.ruleTable.pickedTeamSize,
    registered_sizes: [b.p1.pokemon.length, b.p2.pokemon.length],
    request_flags: [b.p1.activeRequest?.teamPreview, b.p2.activeRequest?.teamPreview],
    requested_sizes: [b.p1.activeRequest?.maxChosenTeamSize, b.p2.activeRequest?.maxChosenTeamSize]};
  if (b.format.mod !== "champions" || preview.request_state !== "teampreview" ||
      preview.picked_team_size !== 3 || preview.registered_sizes.some(n => n !== 6) ||
      preview.request_flags.some(flag => flag !== true) || preview.requested_sizes.some(n => n !== 3))
    throw Error("M-C registered-six Team Preview not active");
  b.makeChoices(commands[0], commands[1]);
  const selected = selectedData(b, registered, selection);
  if (b.requestState !== "move" || selected.some(item => !item.passed))
    throw Error("6-to-3 selected team/order/lead mismatch");
  return {battle: b, commands, preview, selected};
}
function restoration(b) {
  const before = fingerprint(state(b));
  const wire = serialize(b);
  const restored = restoreChecked(wire, before);
  const after = fingerprint(state(restored));
  const initialDiff = diff(b, restored);
  if (!same(b, restored)) throw Error("restored complete state differs: " + initialDiff.first_difference_field);
  const actionA = "move 1", actionB = "switch 2";
  if (restored.requestState !== "move" || !restored.p1.activeRequest?.active?.[0]?.moves?.[0] ||
      !restored.p1.pokemon[1] || !restored.p2.activeRequest?.active?.[0]?.moves?.[0])
    throw Error("two legal branch actions unavailable after selection");
  const actionIds = [actionId(restored, actionA), actionId(restored, actionB)];
  const frozen = fingerprint(state(b)), restoredFrozen = fingerprint(state(restored));
  const branchB = restoreChecked(serialize(restored), restoredFrozen);
  const branchBBefore = fingerprint(state(branchB));
  const branchAResult = resolveFrom(restored, actionA);
  const branchBUntouched = fingerprint(state(branchB)) === branchBBefore;
  const branchBResult = resolveFrom(branchB, actionB);
  const repeatedA = resolveFrom(restored, actionA);
  const independence = {source_unchanged: fingerprint(state(b)) === frozen,
    restored_unchanged: fingerprint(state(restored)) === restoredFrozen,
    branch_b_unchanged_before_execution: branchBUntouched,
    same_action_reproduced: same(branchAResult, repeatedA),
    distinct_action_ids: actionIds[0] !== actionIds[1],
    distinct_results: !same(branchAResult, branchBResult)};
  const branchDiff = diff(branchAResult, branchBResult);
  const replayDiff = diff(branchAResult, repeatedA);
  return {before_sha256: before, after_sha256: after,
    before_state: b.toJSON(), after_state: restored.toJSON(),
    first_difference_field: initialDiff.first_difference_field,
    continuation_actions: [actionA, actionB], action_ids: actionIds,
    continuation_result: {a_sha256: fingerprint(state(branchAResult)),
      b_sha256: fingerprint(state(branchBResult)), repeated_a_sha256: fingerprint(state(repeatedA)),
      branch_first_difference_field: branchDiff.first_difference_field,
      replay_first_difference_field: replayDiff.first_difference_field},
    branch_independence: independence,
    passed: before === after && Object.values(independence).every(Boolean) &&
      initialDiff.first_difference_field === null && replayDiff.first_difference_field === null};
}
function runCase(entry, ctx) {
  const registered = ctx.registered.map(team => structuredClone(team));
  if (entry.kind === "negative_registration")
    registered[0] = mutate(registered[0], entry.mutation, ctx);
  const result = {case_id: entry.id, mechanism: entry.mechanism, evidence_class: "SYNTHETIC_ONLY",
    pinned_showdown_sha: pinned, format: FORMAT, regulation: "M-C",
    champions_master_sha256: masterHashes(), seed: SEED,
    registered_species_ids: registered.map(team => team.map(mon => ctx.dex.species.get(mon.species).id)),
    registered_team_sha256: registered.map(fingerprint),
    registration: registered.map(team => validateRegistration(team, ctx)),
    requested_selection: [entry.p1 || DEFAULT_SELECTION, entry.p2 || DEFAULT_SELECTION],
    selection_status: "NOT_RUN", restoration_status: "NOT_RUN", battle_progressed: false,
    first_difference_field: null, unverified: ["Champions real-game registration and midbattle state"]};
  const [p1, p2] = result.registration;
  if (entry.kind === "negative_registration") {
    result.rejection_stage = p1.champions_master.status === "REJECTED" ?
      "CHAMPIONS_MASTER" : p1.team_validator.status === "REJECTED" ? "TEAM_VALIDATOR" : null;
    result.status = p1.champions_master.status === "REJECTED" && p2.legal ?
      "REJECTED_INVALID_REGISTRATION" : "INCONCLUSIVE";
    result.reason = p1.champions_master.reason || p1.team_validator.reason ||
      "invalid registration unexpectedly accepted";
    return result;
  }
  if (!p1.legal || !p2.legal) {
    result.status = "INCONCLUSIVE";
    result.reason = "source master or TeamValidator rejected supposedly legal team";
    return result;
  }
  try {
    result.selection_commands = result.requested_selection.map((spec, i) =>
      checkSelection(spec, registered[i]));
  } catch (error) {
    result.rejection_stage = "SELECTION_GUARD";
    result.selection_status = "REJECTED";
    result.status = entry.kind === "negative_selection" ? "REJECTED_INVALID_SELECTION" : "INCONCLUSIVE";
    result.reason = error.message;
    return result;
  }
  if (entry.kind === "negative_selection") {
    result.status = "INCONCLUSIVE";
    result.reason = "invalid selection unexpectedly passed guard";
    return result;
  }
  try {
    const {battle, preview, selected} = makeSelectedBattle(registered, result.requested_selection);
    result.preview = preview;
    result.selected = selected;
    result.selection_status = "PASS";
    const beforeTurn = battle.turn;
    const oldLogLength = battle.log.length;
    battle.makeChoices(...entry.opening);
    if (entry.followup) {
      if (battle.requestState !== "move") throw Error("no move request for post-selection switch");
      battle.makeChoices(...entry.followup);
    }
    result.turn_before = beforeTurn;
    result.turn_after = battle.turn;
    result.battle_progressed = battle.turn > beforeTurn &&
      battle.log.slice(oldLogLength).some(line => /^\|(move|switch)\|/.test(line));
    result.post_selection_hp_pp = battle.sides.map(side => side.pokemon.map(mon =>
      ({species: mon.species.id, hp: mon.hp, maxhp: mon.maxhp,
        moves: mon.moveSlots.map(slot => ({id: slot.id, pp: slot.pp, maxpp: slot.maxpp}))})));
    result.active_after_progress = battle.sides.map(side => side.active[0]?.species.id);
    result.switch_event_count = battle.log.slice(oldLogLength)
      .filter(line => line.startsWith("|switch|")).length;
    if (!result.battle_progressed || battle.requestState !== "move" ||
        selected.some((item, i) =>
          item.unselected.some(id => battle.sides[i].pokemon.some(mon => mon.species.id === id))))
      throw Error("turn did not advance to an isolated selected-three move request");
    result.restoration = restoration(battle);
    result.restoration_status = result.restoration.passed ? "PASS_SYNTHETIC" : "STATE_OR_QUEUE_MISMATCH";
    result.first_difference_field = result.restoration.first_difference_field ||
      result.restoration.continuation_result.replay_first_difference_field;
    result.status = result.restoration.passed ? "PASS_SYNTHETIC" : "STATE_OR_QUEUE_MISMATCH";
    result.reason = result.restoration.passed ?
      "legal registered six, exact three selected, complete snapshot and branches reproduced" :
      "restored or forked state diverged";
  } catch (error) {
    result.status = result.selection_status === "PASS" ? "STATE_OR_QUEUE_MISMATCH" : "SELECTION_MISMATCH";
    result.reason = error.message;
  }
  return result;
}
function run() {
  assertManifest();
  const ctx = context();
  const results = manifest.cases.map(entry => runCase(entry, ctx));
  const positives = results.filter(r => r.case_id.startsWith("REG-"));
  const negatives = results.filter(r => r.case_id.startsWith("NEG-"));
  const statuses = ["PASS_SYNTHETIC", "REJECTED_INVALID_REGISTRATION",
    "REJECTED_INVALID_SELECTION", "SELECTION_MISMATCH", "STATE_OR_QUEUE_MISMATCH",
    "INCONCLUSIVE", "UNSUPPORTED"];
  return {schema: "phase0-six-team-selection-diagnostics-v1", pinned_showdown_sha: pinned,
    champions_master_sha256: masterHashes(), format: FORMAT, regulation: "M-C",
    gate_A: "UNKNOWN", gate_B: "UNSUPPORTED", adoption: "NO_GO",
    registered_team_pair_count: 1,
    selection_pattern_count: new Set(positives.map(r => stableStringify(r.requested_selection))).size,
    distinct_input_state_count: new Set(positives.map(r => r.restoration?.before_sha256).filter(Boolean)).size,
    positive_case_count: positives.length, negative_case_count: negatives.length,
    repetitions: 0, representative_100_cases_met: false,
    counts: Object.fromEntries(statuses.map(s => [s, results.filter(r => r.status === s).length])),
    results};
}
if (require.main === module) {
  try {process.stdout.write(JSON.stringify(run()) + "\n");}
  catch (error) {process.stdout.write(JSON.stringify({status: "NOT_RUN", reason: error.message}) + "\n"); process.exitCode = 2;}
}
module.exports = {run, runCase, context, validateRegistration, checkSelection,
  makeSelectedBattle, restoration, assertManifest};
