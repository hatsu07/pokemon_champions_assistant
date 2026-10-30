"use strict";

// Phase-0-only isolated runner. Never imported by the Windows assistant.
// All observations injected here are SYNTHETIC and are not Champions game evidence.
const readline = require("node:readline");
const path = require("node:path");
const crypto = require("node:crypto");
const fs = require("node:fs");

const pinned = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const root = process.env.SHOWDOWN_ROOT;
let Battle, loadError = null;
try {
  if (!root || !fs.existsSync(path.join(root, "dist", "sim", "index.js"))) {
    throw new Error("SHOWDOWN_ROOT must contain a prebuilt pinned Showdown dist/sim/index.js");
  }
  ({ Battle } = require(path.join(root, "dist", "sim", "index.js")));
} catch (error) {
  loadError = String(error.message);
}

function set(species, moves, item = "Leftovers", ability = undefined) {
  return {name: species, species, level: 50, nature: "Jolly", item, ability,
    evs: {hp: 2, atk: 32, def: 0, spa: 0, spd: 0, spe: 32}, moves};
}
const own = [
  set("Garchomp", ["Earthquake", "Dragon Claw", "Rock Slide", "Swords Dance"], "Choice Scarf", "Rough Skin"),
  set("Primarina", ["Moonblast", "Sparkling Aria", "Aqua Jet", "Encore"], "Leftovers", "Torrent"),
  set("Mimikyu", ["Play Rough", "Shadow Sneak", "Shadow Claw", "Swords Dance"], "Life Orb", "Disguise"),
];
function opponents(hypothesis) {
  return [
    set("Hippowdon", hypothesis
      ? ["Yawn", "Earthquake", "Slack Off", "Stealth Rock"]
      : ["Earthquake", "Stealth Rock", "Slack Off", "Yawn"],
    hypothesis ? "Rocky Helmet" : "Sitrus Berry", "Sand Stream"),
    set("Corviknight", ["Roost", "U-turn", "Iron Defense", "Body Press"], "Leftovers", "Pressure"),
    set("Lucario", ["Aura Sphere", "Dark Pulse", "Nasty Plot", "Flash Cannon"], "Lucarionite", "Inner Focus"),
  ];
}
function create(seed, hypothesis = 0, runOpeningTurn = true) {
  const b = new Battle({formatid: "gen9championsbssregmc", seed,
    p1: {name: "Synthetic P1", team: own},
    p2: {name: "Synthetic P2", team: opponents(hypothesis)}, strictChoices: true});
  if (b.format.mod !== "champions") throw new Error("Champions mod is not active");
  if (b.requestState === "teampreview") b.makeChoices("team 123", "team 123");
  if (b.p1.pokemon.length !== 3 || b.p2.pokemon.length !== 3)
    throw new Error("expected selected 3 vs 3");
  if (runOpeningTurn && b.requestState === "move") b.makeChoices("move 1", "move 1");
  return b;
}
function projection(b) {
  // Serialized values include effect durations, lock targets and pending actions.
  // toJSON normalizes references; comparing only effect keys would miss state loss.
  return {
    persisted_state: b.toJSON(),
    turn: b.turn, requestState: b.requestState, rng: b.prng.getSeed(),
    weather: b.field.weather, pseudoWeather: Object.keys(b.field.pseudoWeather).sort(),
    sides: b.sides.map(s => ({
      conditions: Object.keys(s.sideConditions).sort(),
      active: s.active.map(p => p && p.position),
      pokemon: s.pokemon.map(p => ({
        species: p.species.id, hp: p.hp, maxhp: p.maxhp, fainted: p.fainted,
        status: p.status, item: p.item, ability: p.ability,
        boosts: p.boosts, moves: p.moveSlots.map(m => [m.id, m.pp]),
        volatiles: Object.keys(p.volatiles).sort(), switchFlag: !!p.switchFlag,
      })),
    })),
  };
}
function firstDifferentPaths(a, b, prefix = "$", out = []) {
  if (out.length >= 12 || Object.is(a, b)) return out;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" ||
    Array.isArray(a) !== Array.isArray(b)) {
    out.push(prefix);
    return out;
  }
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (out.length >= 12) break;
    firstDifferentPaths(a[key], b[key], prefix + "." + key, out);
  }
  return out;
}
function digest(b) {
  return crypto.createHash("sha256").update(JSON.stringify(projection(b))).digest("hex");
}
function copy(b) { return Battle.fromJSON(JSON.parse(JSON.stringify(b.toJSON()))); }
function alter(b, kind) {
  const a = b.p1.active[0];
  if (!a) throw new Error("no active pokemon");
  switch (kind) {
    case "normal": break;
    case "hp_status_pp_boost":
      a.hp = Math.max(1, a.hp - 8);
      a.status = "par";
      a.boosts.atk = 1;
      a.moveSlots[0].pp = Math.max(0, a.moveSlots[0].pp - 2);
      break;
    case "item_choice":
      // A real Choice Scarf user performed a move before this injection.
      if (a.item !== "choicescarf") throw new Error("choice user mismatch");
      if (!a.volatiles.choicelock) throw new Error("choice lock absent in sampled state");
      break;
    case "weather_screens":
      b.field.setWeather("raindance", a);
      b.p1.addSideCondition("reflect", a);
      break;
    case "consumed_item":
      a.item = "";
      break;
    case "forced_switch":
      a.switchFlag = true;
      b.makeRequest("switch");
      break;
    case "mega_history":
      // Arbitrary post-mega state injection is NOT supported by this PoC.
      throw new Error("UNSUPPORTED: independent reconstruction of mega history");
    default: throw new Error("unknown fixture");
  }
}
// Only public, actually visible synthetic board fields. Opponent hidden moves,
// item and ability are *not* promoted into an observation.
function observed(b) {
  return {turn: b.turn, requestState: b.requestState,
    weather: b.field.weather, sides: b.sides.map(s => ({
      conditions: Object.keys(s.sideConditions).sort(),
      active: s.active.map(p => p && ({
        species: p.species.id, hp: p.hp, status: p.status,
        boosts: p.boosts, volatiles: Object.keys(p.volatiles).sort(),
      })),
    }))};
}
function assertInjected(b, kind) {
  const a = b.p1.active[0];
  switch (kind) {
    case "hp_status_pp_boost":
      if (a.status !== "par" || a.boosts.atk !== 1 || a.hp >= a.maxhp ||
        a.moveSlots[0].pp >= a.moveSlots[0].maxpp) throw Error("status/AP/HP/PP injection lost");
      break;
    case "item_choice":
      if (a.item !== "choicescarf" || !a.volatiles.choicelock ||
        !a.volatiles.choicelock.move) throw Error("choice lock or target move lost");
      break;
    case "weather_screens":
      if (b.field.weather !== "raindance" || !b.p1.sideConditions.reflect ||
        !(b.field.weatherState.duration > 0) ||
        !(b.p1.sideConditions.reflect.duration > 0))
        throw Error("weather/reflect duration or effect missing");
      break;
    case "consumed_item":
      if (a.item !== "") throw Error("consumed item was restored");
      break;
    case "forced_switch":
      if (b.requestState !== "switch" || !a.switchFlag) throw Error("forced switch request lost");
      break;
  }
}
function executeBranch(snapshot, kind, progress = {}) {
  const c = copy(snapshot);
  const before = digest(c);
  const beforeTurn = c.turn;
  const beforeActive = c.p1.active[0]?.species.id;
  if (kind === "forced_switch") {
    if (c.requestState !== "switch") throw Error("switch request expected");
    if (!c.choose("p1", "switch 2")) throw Error("forced switch choice illegal");
    progress.choice_legal = 1;
    if (c.p1.active[0]?.species.id === beforeActive) throw Error("switch unresolved");
    if (c.p1.active[0]?.species.id !== "primarina") throw Error("unexpected switch destination");
  } else {
    if (c.requestState !== "move") throw Error("move request expected");
    if (!c.choose("p1", "move 1")) throw Error("player 1 move illegal");
    if (!c.choose("p2", "move 1")) throw Error("player 2 move illegal");
    progress.choice_legal = 1;
    if (c.turn <= beforeTurn && c.requestState === "move") throw Error("move turn not resolved");
  }
  if (digest(c) === before) throw Error("legal action produced unchanged board");
  return c;
}
function testCase(b, kind) {
  const milestones = {serialized_ok: 0, reconstructed_ok: 0, choice_legal: 0, branch_resolved: 0};
  try {
    const original = digest(b);
    const isolated = copy(b);
    alter(isolated, kind);
    const expected = digest(isolated);
    const serializedBefore = JSON.stringify(isolated.toJSON());
    milestones.serialized_ok = 1;
    const reconstructed = copy(isolated);
    if (digest(reconstructed) !== expected ||
      JSON.stringify(reconstructed.toJSON()) !== serializedBefore)
      throw Error("full serialized state roundtrip mismatch (including queue/effect values)");
    assertInjected(reconstructed, kind);
    milestones.reconstructed_ok = 1;
    if (digest(b) !== original) throw Error("source state mutated");
    const a = executeBranch(reconstructed, kind, milestones);
    const a2 = executeBranch(reconstructed, kind);
    if (digest(a) !== digest(a2)) throw Error("seeded branch diverged");
    let nextTurnVerified = false;
    if (a.requestState === "move" && a2.requestState === "move") {
      const nextA = executeBranch(a, "normal");
      const nextB = executeBranch(a2, "normal");
      if (digest(nextA) !== digest(nextB)) throw Error("next-turn seeded branch diverged");
      nextTurnVerified = true;
    }
    if (digest(reconstructed) !== expected || digest(b) !== original)
      throw Error("branch mutated frozen source");
    milestones.branch_resolved = 1;
    return {status: "PASS_SYNTHETIC", digest: expected, milestones,
      branch_digest: digest(a), next_turn_verified: nextTurnVerified,
      serialized_values_compared: true, action_kind: kind === "forced_switch" ? "switch" : "move"};
  } catch (error) {
    error.milestones = {...milestones};
    throw error;
  }
}
function probe() {
  const results = {};
  const b = create([1, 2, 3, 4]);
  for (const kind of ["normal", "hp_status_pp_boost", "item_choice", "weather_screens",
    "consumed_item", "forced_switch", "mega_history"]) {
    try { results[kind] = testCase(b, kind); }
    catch (e) { results[kind] = {status: "UNSUPPORTED", detail: String(e.message)}; }
  }
  let pendingDiagnostics = {};
  try {
    // A legal choice accepted before the other player's choice must survive restore.
    const pending = create([2, 7, 1, 8], 0, false);
    if (!pending.choose("p1", "move 1")) throw Error("pending choice illegal");
    if (!pending.p1.choice.actions?.length) throw Error("pending choice missing");
    const beforeState = pending.toJSON();
    const recovered = copy(pending);
    const afterState = recovered.toJSON();
    const differentPaths = firstDifferentPaths(beforeState, afterState);
    const recoveredActions = recovered.p1.choice.actions?.length || 0;
    pendingDiagnostics = {different_serialized_paths: differentPaths,
      original_choice_actions: pending.p1.choice.actions.length,
      restored_choice_actions: recoveredActions};
    if (!recoveredActions) throw Error("pending choice actions are absent after restore");
    if (!pending.choose("p2", "move 1") || !recovered.choose("p2", "move 1"))
      throw Error("pending second choice illegal");
    const equalOutcome = digest(pending) === digest(recovered);
    pendingDiagnostics.equal_resolved_outcome = equalOutcome;
    if (differentPaths.length || !equalOutcome)
      throw Error("pending snapshot not proven equivalent after restore; see diagnostic paths");
    results.pending_choice = {status: "PASS_SYNTHETIC", serialized_pending_action: true,
      resolved_same_after_copy: true, ...pendingDiagnostics};
  } catch (e) { results.pending_choice = {status: "UNSUPPORTED", restoration_status: "UNSUPPORTED",
      detail: String(e.message), safe_for_midbattle_reconstruction: false, ...pendingDiagnostics}; }
  try {
    // Freeze a common observable board before *either* unknown set acts.
    const a = create([9, 8, 7, 6], 0, false);
    const other = create([9, 8, 7, 6], 1, false);
    const before = digest(a), second = digest(other);
    const publicBefore = JSON.stringify(observed(a));
    if (publicBefore !== JSON.stringify(observed(other))) throw Error("different observed input");
    if (a.requestState !== "move" || other.requestState !== "move") throw Error("not at common move turn");
    const resolvedA = executeBranch(a, "normal");
    const resolvedB = executeBranch(other, "normal");
    const outcomesDiffer = JSON.stringify(observed(resolvedA)) !== JSON.stringify(observed(resolvedB));
    if (!outcomesDiffer) throw Error("distinct hypotheses did not change visible result");
    if (digest(a) !== before || digest(other) !== second ||
      JSON.stringify(observed(a)) !== publicBefore ||
      JSON.stringify(observed(other)) !== publicBefore) throw Error("hypotheses polluted observed state");
    results.unknown_sets = {status: "PASS_SYNTHETIC", same_observed_input: true,
      same_own_action: "p1 move 1 (Earthquake) under both hypotheses",
      opponent_actions: ["p2 move 1 (Earthquake)", "p2 move 1 (Yawn)"],
      same_move_slot_not_same_opponent_move_id: true, observed_outcomes_differ: true,
      sources_unchanged: true, changed_initial_model: before !== second,
      branch_1: digest(resolvedA), branch_2: digest(resolvedB)};
  } catch (e) { results.unknown_sets = {status: "UNSUPPORTED", detail: String(e.message)}; }
  return {engine_sha: pinned, format: b.format.id, format_mod: b.format.mod,
    sample: "synthetic; not an observed Champions game", results};
}
function benchmark(count) {
  if (!Number.isInteger(count) || count < 100 || count > 10000) throw Error("count must be 100..10000");
  const kinds = ["normal", "hp_status_pp_boost", "item_choice", "weather_screens", "consumed_item", "forced_switch"];
  const base = create([11, 22, 33, 44]);
  const warmup = [];
  for (let i = 0; i < 20; i++) {
    const t = process.hrtime.bigint(); copy(base);
    warmup.push(Number(process.hrtime.bigint() - t) / 1e6);
  }
  const counts = {requested: count, serialized_ok: 0, reconstructed_ok: 0,
    choice_legal: 0, branch_resolved: 0, unsupported: 0};
  const by_kind = Object.fromEntries(kinds.map(k => [k, {requested: 0,
    serialized_ok: 0, reconstructed_ok: 0, choice_legal: 0, branch_resolved: 0, unsupported: 0}]));
  const samples = [], unsupported = {};
  for (let i = 0; i < count; i++) {
    const kind = kinds[i % kinds.length], group = by_kind[kind];
    group.requested++;
    const t = process.hrtime.bigint();
    let status = "UNSUPPORTED", milestones = {}, error = null;
    try {
      const result = testCase(base, kind);
      status = result.status;
      milestones = result.milestones || {};
    } catch (e) { error = String(e.message); milestones = e.milestones || {}; unsupported[kind] = (unsupported[kind] || 0) + 1; }
    for (const field of ["serialized_ok", "reconstructed_ok", "choice_legal", "branch_resolved"]) {
      if (milestones[field]) {counts[field]++; group[field]++;}
    }
    if (status !== "PASS_SYNTHETIC" || !milestones.branch_resolved) {
      counts.unsupported++; group.unsupported++;
      if (!error) error = "branch not resolved";
    }
    samples.push({kind, ms: Number(process.hrtime.bigint() - t) / 1e6,
      status, milestones, ...(error ? {error} : {})});
  }
  const numbers = samples.filter(x => x.milestones.branch_resolved).map(x => x.ms).sort((a, b) => a - b);
  const percentile = q => numbers.length ? numbers[Math.ceil(q * numbers.length) - 1] : null;
  return {requested: count, completed: counts.branch_resolved, counts, by_kind, unsupported,
    warmup_ms: warmup, p50_ms: percentile(.5), p95_ms: percentile(.95),
    p99_ms: percentile(.99), memory: process.memoryUsage(), timings: samples,
    measurement_scope: "synthetic serialization + legal action resolution, prebuilt Node only; NOT Windows UI E2E"};
}
function handle(message) {
  if (loadError) return {status: "UNSUPPORTED", reason: loadError};
  if (message.method === "probe") return {status: "OK", ...probe()};
  if (message.method === "benchmark") return {status: "OK", ...benchmark(message.count)};
  return {status: "UNSUPPORTED", reason: "unknown method"};
}
const input = readline.createInterface({input: process.stdin});
input.on("line", line => {
  try {
    const req = JSON.parse(line);
    const result = handle(req);
    process.stdout.write(JSON.stringify({id: req.id, ...result}) + "\n");
  } catch (e) {
    let id = null;
    try { id = JSON.parse(line).id; } catch {}
    process.stdout.write(JSON.stringify({id, status: "UNSUPPORTED", reason: String(e.message)}) + "\n");
  }
});
