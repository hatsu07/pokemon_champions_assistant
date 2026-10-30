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
      ? ["Earthquake", "Yawn", "Slack Off", "Stealth Rock"]
      : ["Earthquake", "Stealth Rock", "Slack Off", "Yawn"],
    hypothesis ? "Rocky Helmet" : "Sitrus Berry", "Sand Stream"),
    set("Corviknight", ["Roost", "U-turn", "Iron Defense", "Body Press"], "Leftovers", "Pressure"),
    set("Lucario", ["Aura Sphere", "Dark Pulse", "Nasty Plot", "Flash Cannon"], "Lucarionite", "Inner Focus"),
  ];
}
function create(seed, hypothesis = 0) {
  const b = new Battle({formatid: "gen9championsbssregmc", seed,
    p1: {name: "Synthetic P1", team: own},
    p2: {name: "Synthetic P2", team: opponents(hypothesis)}, strictChoices: true});
  if (b.format.mod !== "champions") throw new Error("Champions mod is not active");
  if (b.requestState === "teampreview") b.makeChoices("team 123", "team 123");
  if (b.p1.pokemon.length !== 3 || b.p2.pokemon.length !== 3)
    throw new Error("expected selected 3 vs 3");
  if (b.requestState === "move") b.makeChoices("move 1", "move 1");
  return b;
}
function projection(b) {
  return {
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
function fork(b, p1, p2) {
  const c = copy(b);
  if (c.requestState !== "move") throw new Error("not a move window");
  c.makeChoices(p1, p2);
  return c;
}
function testCase(b, kind) {
  const original = digest(b);
  const isolated = copy(b);
  alter(isolated, kind);
  const expected = digest(isolated);
  const reconstructed = copy(isolated);
  if (digest(reconstructed) !== expected) throw new Error("roundtrip mismatch");
  if (digest(b) !== original) throw new Error("source state mutated");
  if (kind === "normal" || kind === "hp_status_pp_boost" || kind === "weather_screens" || kind === "consumed_item" || kind === "item_choice") {
    if (reconstructed.requestState === "move") {
      const a = fork(reconstructed, "move 1", "move 1");
      const a2 = fork(reconstructed, "move 1", "move 1");
      if (digest(a) !== digest(a2)) throw new Error("seeded branch diverged");
      if (digest(reconstructed) !== expected) throw new Error("branch mutated snapshot");
    }
  }
  return {status: "PASS_SYNTHETIC", digest: expected};
}
function probe() {
  const results = {};
  const b = create([1, 2, 3, 4]);
  for (const kind of ["normal", "hp_status_pp_boost", "item_choice", "weather_screens",
    "consumed_item", "forced_switch", "mega_history"]) {
    try { results[kind] = testCase(b, kind); }
    catch (e) { results[kind] = {status: "UNSUPPORTED", detail: String(e.message)}; }
  }
  try {
    const a = create([9, 8, 7, 6], 0), other = create([9, 8, 7, 6], 1);
    const before = digest(a), second = digest(other);
    const fork1 = copy(a), fork2 = copy(other);
    if (digest(a) !== before || digest(other) !== second) throw new Error("hypothesis mutated");
    results.unknown_sets = {status: "PASS_SYNTHETIC", independently_instantiated: true,
      changed_initial_model: before !== second,
      branch_1: digest(fork1), branch_2: digest(fork2)};
  } catch (e) { results.unknown_sets = {status: "UNSUPPORTED", detail: String(e.message)}; }
  return {engine_sha: pinned, format: b.format.id, format_mod: b.format.mod,
    sample: "synthetic; not an observed Champions game", results};
}
function benchmark(count) {
  if (!Number.isInteger(count) || count < 100 || count > 10000) throw new Error("count must be 100..10000");
  const kinds = ["normal", "hp_status_pp_boost", "item_choice", "weather_screens", "consumed_item", "forced_switch"];
  const base = create([11, 22, 33, 44]);
  const warmup = [];
  for (let i = 0; i < 20; i++) {
    const t = process.hrtime.bigint(); copy(base);
    warmup.push(Number(process.hrtime.bigint() - t) / 1e6);
  }
  const samples = [], unsupported = {};
  for (let i = 0; i < count; i++) {
    const kind = kinds[i % kinds.length];
    const t = process.hrtime.bigint();
    try {
      const sample = copy(base);
      alter(sample, kind);
      const c = copy(sample);
      if (digest(sample) !== digest(c)) throw new Error("roundtrip mismatch");
      if (c.requestState === "move") fork(c, "move 1", "move 1");
      samples.push({kind, ms: Number(process.hrtime.bigint() - t) / 1e6});
    } catch (e) {
      unsupported[kind] = (unsupported[kind] || 0) + 1;
    }
  }
  const numbers = samples.map(x => x.ms).sort((a, b) => a - b);
  const percentile = q => numbers.length ? numbers[Math.ceil(q * numbers.length) - 1] : null;
  return {requested: count, completed: samples.length, unsupported, warmup_ms: warmup,
    p50_ms: percentile(.5), p95_ms: percentile(.95), p99_ms: percentile(.99),
    memory: process.memoryUsage(), timings: samples,
    measurement_scope: "isolated prebuilt Node engine only; NOT Windows UI E2E"};
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
