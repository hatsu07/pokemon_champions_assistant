"use strict";
// Offline, isolated Phase 0 diagnostic. No live recommendations or game controls.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {execFileSync} = require("node:child_process");
const {rows, speciesKeyForRow, cappedSourcePP, types} = require("./audit.cjs");

const PIN = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT = "gen9championsbssregmc";
const STATS = ["hp", "atk", "def", "spa", "spd", "spe"];
const root = process.env.SHOWDOWN_ROOT;
const {Battle, Dex} = require(path.join(root || "", "dist", "sim", "index.js"));
const dex = Dex.forFormat(FORMAT);
const schema = JSON.parse(fs.readFileSync(path.join(__dirname, "golden_schema.json"), "utf8"));
const MASTER_FILES = ["Pokemon_Champions_MC_move_master.csv", "Pokemon_Champions_MC_usable_pokemon.csv",
  "Pokemon_Champions_MC_pokemon_learnsets_linked.csv", "Pokemon_Champions_MC_usable_held_items.csv"];
const WEATHER_IDS = new Set(["", "raindance", "sunnyday", "sandstorm", "snowscape"]);
const STATUS_IDS = new Set(["", "brn", "par", "psn", "tox", "slp", "frz"]);
const masterBase = path.resolve(process.env.CHAMPIONS_MASTERS_DIR || path.join(__dirname, "masters"));
const sha256 = data => crypto.createHash("sha256").update(data).digest("hex");
let sourceCache;

function sources() {
  if (sourceCache) return sourceCache;
  const data = Object.fromEntries(MASTER_FILES.map(name => [name, fs.readFileSync(path.join(masterBase, name))]));
  const master_sha256 = Object.fromEntries(MASTER_FILES.map(name => [name, sha256(data[name])]));
  const [moves, pokemon, links, heldItems] = MASTER_FILES.map(name => rows(data[name].toString("utf8")));
  const pokemonByName = new Map(pokemon.map(r =>
    [r["ポケモン名"] + "|" + r["フォーム/姿"] + "|" + r["区分"], r]));
  const speciesRows = new Map();
  const allSpecies = dex.species.all();
  for (const link of links) {
    const row = pokemonByName.get(link["ポケモン名"] + "|" + link["フォーム/姿"] + "|" + link["区分"]);
    if (!row) continue;
    const expectedTypes = [row["タイプ1"], row["タイプ2"]].filter(Boolean).map(x => types[x]);
    if (expectedTypes.some(x => !x)) continue;
    const candidates = allSpecies.filter(s => dex.species.get(s.baseSpecies).id === link.LearnsetKey &&
      !!s.isMega === (row["区分"] === "メガシンカ") &&
      STATS.every((stat, i) => s.baseStats[stat] === Number(row[["H", "A", "B", "C", "D", "S"][i]])) &&
      s.types.join("/") === expectedTypes.join("/"));
    const preferred = speciesKeyForRow(row, link.LearnsetKey);
    const selected = row["区分"] !== "メガシンカ" && candidates.find(s => s.id === preferred) ||
      (candidates.length === 1 ? candidates[0] : null);
    if (!selected) continue; // ambiguous or absent source mapping: fail closed
    const entry = {row, link, moves: new Set(link["習得MoveKey一覧"].split(",").map(x => x.trim()))};
    speciesRows.set(selected.id, speciesRows.has(selected.id) ? null : entry);
  }
  sourceCache = {master_sha256, moves: new Map(moves.map(r => [r.MoveKey, r])), speciesRows,
    items: new Set(heldItems.map(r => dex.items.get(r["英語名"]).id))};
  return sourceCache;
}

function identity(spec, ev, missing) {
  const species = fstr(spec, "species", ev, missing);
  const form = fstr(spec, "form", ev, missing, true);
  const mega = fact(spec?.mega_state, "mega_state", ev, missing, "boolean", true);
  const mon = species && dex.species.get(species);
  if (species && !mon.exists) missing.push("unknown species " + species);
  const mapped = mon?.exists && sources().speciesRows.get(mon.id);
  if (mon?.exists && !mapped) missing.push("species/form absent or ambiguous in Champions master: " + species);
  if (mapped && form !== undefined && form !== mapped.row["フォーム/姿"] && form !== (mon.forme || ""))
    missing.push("form contradicts exact species key and Champions master");
  if (mon?.exists && mega !== undefined && mega !== !!mon.isMega)
    missing.push("mega state contradicts exact species key");
  return {mon, mapped};
}
function legalMove(species, moveId, missing) {
  const mapped = sources().speciesRows.get(dex.species.get(species).id);
  if (!mapped) {missing.push("species absent from Champions learnset"); return;}
  const learned = dex.species.getLearnsetData(mapped.link.LearnsetKey)?.learnset || {};
  if (!mapped.moves.has(moveId) || !Object.hasOwn(learned, moveId))
    missing.push("move not in Champions master and pinned learnset for " + species + ": " + moveId);
}

function validateShape(value, rule, at = "$") {
  if (rule.$ref) return validateShape(value, schema.$defs[rule.$ref.split("/").at(-1)], at);
  if (rule.const !== undefined && value !== rule.const) throw Error(at + ": wrong schema version");
  if (rule.enum && !rule.enum.includes(value)) throw Error(at + ": invalid enum");
  if (rule.type) {
    const isObject = value !== null && typeof value === "object" && !Array.isArray(value);
    const valid = rule.type === "object" ? isObject : rule.type === "array" ? Array.isArray(value) :
      typeof value === rule.type;
    if (!valid) throw Error(at + ": expected " + rule.type);
  }
  if (rule.minLength && value.length < rule.minLength) throw Error(at + ": empty string");
  if (rule.format === "date-time" && !Number.isFinite(Date.parse(value))) throw Error(at + ": invalid date-time");
  if (rule.type === "array") for (let i = 0; i < value.length; i++) validateShape(value[i], rule.items, at + "[" + i + "]");
  if (rule.type === "object") {
    for (const key of rule.required || []) if (!Object.hasOwn(value, key)) throw Error(at + ": missing " + key);
    if (rule.minProperties && Object.keys(value).length < rule.minProperties) throw Error(at + ": empty object");
    for (const [key, child] of Object.entries(value)) {
      if (!Object.hasOwn(rule.properties || {}, key)) {
        if (rule.additionalProperties === false) throw Error(at + ": unexpected " + key);
      } else validateShape(child, rule.properties[key], at + "." + key);
    }
  }
}

function assertPinned() {
  if (!root) throw Error("SHOWDOWN_ROOT required");
  const actual = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {encoding: "utf8"}).trim();
  if (actual !== PIN || dex.currentMod !== "champions")
    throw Error(`Pinned Champions source mismatch: ${actual}, mod ${dex.currentMod}`);
}
function fact(f, label, evidence, missing, type, optional = false) {
  if (f === undefined && optional) return undefined;
  if (!f || typeof f !== "object" || Array.isArray(f) ||
      !["GAME_OBSERVED", "INFERRED", "UNKNOWN", "SYNTHETIC"].includes(f.source)) {
    missing.push(label + ": missing or invalid fact/source"); return undefined;
  }
  if (optional && f.source === "UNKNOWN") return undefined;
  if (f.source === "UNKNOWN" || !Object.hasOwn(f, "value")) {
    missing.push(label + ": unknown"); return undefined;
  }
  if (f.source === "INFERRED" ||
      (evidence.origin === "REAL_GAME" && f.source !== "GAME_OBSERVED") ||
      (evidence.origin === "SYNTHETIC" && f.source !== "SYNTHETIC")) {
    missing.push(label + ": inferred or source conflicts with case origin"); return undefined;
  }
  if (typeof f.value !== type || (type === "number" && !Number.isInteger(f.value))) {
    missing.push(label + ": invalid value"); return undefined;
  }
  if (evidence.origin === "REAL_GAME" && (!Array.isArray(f.evidence_ids) ||
      !f.evidence_ids.length || f.evidence_ids.some(id => !evidence.references.some(r => r.id === id)))) {
    missing.push(label + ": no linked independent evidence"); return undefined;
  }
  return f.value;
}
function fnum(obj, key, ev, missing, optional = false) {
  return fact(obj?.[key], key, ev, missing, "number", optional);
}
function fstr(obj, key, ev, missing, optional = false) {
  return fact(obj?.[key], key, ev, missing, "string", optional);
}
function mon(spec, ev, missing, requireEffects = true) {
  const {mon: speciesRecord, mapped} = identity(spec, ev, missing);
  const species = speciesRecord?.id;
  const level = fnum(spec, "level", ev, missing);
  const nature = fstr(spec, "nature", ev, missing);
  const ability = requireEffects ? fstr(spec, "ability", ev, missing) : "";
  const item = requireEffects ? fstr(spec, "item", ev, missing) : "";
  const ap = {};
  for (const s of STATS) ap[s] = fnum(spec?.ap, s, ev, missing);
  if (level !== undefined && level !== 50) missing.push("M-C ranked comparison requires level 50");
  if (Object.values(ap).some(v => v !== undefined && (v < 0 || v > 32)))
    missing.push("Champions AP must be 0..32 per stat");
  if (Object.values(ap).every(v => v !== undefined) && Object.values(ap).reduce((a, b) => a + b, 0) > 66)
    missing.push("Champions AP total exceeds 66");
  if (requireEffects && !ability) missing.push("ability must be explicitly known");
  if (nature && !dex.natures.get(nature).exists) missing.push("unknown nature " + nature);
  if (ability && !dex.abilities.get(ability).exists) missing.push("unknown ability " + ability);
  if (ability && mapped && !Object.values(speciesRecord.abilities).some(x =>
    dex.abilities.get(x).id === dex.abilities.get(ability).id))
    missing.push("ability not legal for Champions species/form: " + ability);
  if (item && !dex.items.get(item).exists) missing.push("unknown item " + item);
  if (item && dex.items.get(item).exists && !sources().items.has(dex.items.get(item).id))
    missing.push("item absent from Champions held-item master: " + item);
  if (missing.length) return null;
  const firstMove = mapped.moves.values().next().value;
  if (!firstMove) {missing.push("empty Champions learnset"); return null;}
  return {species, level, nature, ability, item, evs: ap, moves: [firstMove]};
}
function battle(attacker, defender) {
  const fillers = [{species: "Garchomp", level: 50, nature: "Serious", ability: "Rough Skin",
    item: "", evs: {hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0}, moves: ["Earthquake"]}];
  const b = new Battle({formatid: FORMAT, seed: [1, 2, 3, 4],
    p1: {name: "Golden attacker", team: [attacker, ...fillers, ...fillers]},
    p2: {name: "Golden defender", team: [defender, ...fillers, ...fillers]}, strictChoices: true});
  if (b.requestState === "teampreview") b.makeChoices("team 123", "team 123");
  if (b.format.mod !== "champions") throw Error("Champions mod inactive");
  return b;
}
function result(kind, expected, observed, evidence, missing, detail = {}) {
  if (missing.length) return {kind, status: "INCONCLUSIVE", missing: [...new Set(missing)], ...detail};
  const match = Array.isArray(detail.rolls) ? detail.rolls.includes(observed) :
    Array.isArray(expected) ? observed >= expected[0] && observed <= expected[1] : expected === observed;
  const scope = evidence.origin === "SYNTHETIC" ? "SYNTHETIC" :
    evidence.verification === "USER_VERIFIED" ? "" : "UNVERIFIED";
  return {kind, status: (match ? "MATCH" : "MISMATCH") + (scope ? "_" + scope : ""),
    observed, showdown: expected, ...detail};
}
function statsCheck(check, ev) {
  const missing = [];
  const set = mon(check.pokemon, ev, missing, false);
  const observed = {};
  for (const s of STATS) observed[s] = fnum(check.observed_stats, s, ev, missing);
  if (STATS.some(s => observed[s] !== undefined && observed[s] < 1)) missing.push("invalid observed stat");
  if (missing.length) return result("stats", null, null, ev, missing);
  const p = battle(set, set).p1.pokemon[0];
  const actual = {hp: p.maxhp, ...p.baseStoredStats};
  return STATS.map(s => result("stats." + s, actual[s], observed[s], ev, [],
    {species: set.species, level: set.level}));
}
function ppCheck(check, ev) {
  const missing = [];
  const {mon: speciesRecord, mapped} = identity(check.pokemon, ev, missing);
  const moveId = fstr(check, "move_id", ev, missing);
  const battleMax = fnum(check, "observed_battle_max_pp", ev, missing);
  const remaining = fnum(check, "observed_remaining_pp", ev, missing, true);
  const observedBase = fnum(check, "observed_base_pp", ev, missing, true);
  const move = moveId && dex.moves.get(moveId);
  if (moveId && !move.exists) missing.push("unknown move " + moveId);
  if (move?.exists && mapped) legalMove(speciesRecord.id, move.id, missing);
  if (remaining !== undefined && battleMax !== undefined && remaining > battleMax)
    missing.push("remaining PP exceeds observed maximum");
  if ([battleMax, remaining, observedBase].some(v => v !== undefined && v < 0))
    missing.push("negative PP");
  if (missing.length) return result("pp", null, null, ev, missing);
  const row = sources().moves.get(move.id);
  if (!row || !/^\d+$/.test(row.PP)) return result("pp", null, null, ev, ["source raw PP unavailable"]);
  const raw = Number(row.PP), capped = cappedSourcePP(raw);
  const b = battle({species: speciesRecord.id, level: 50, nature: "Serious",
    ability: Object.values(speciesRecord.abilities)[0], item: "",
    evs: {hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0}, moves: [move.id]},
    {species: "Garchomp", level: 50, nature: "Serious", ability: "Rough Skin",
      item: "", evs: {hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0}, moves: ["Earthquake"]});
  const slot = b.p1.pokemon[0].moveSlots.find(s => s.id === move.id);
  if (!slot) return result("pp", null, null, ev, ["move not initialized in battle"]);
  const details = {move_id: move.id, master_raw_pp: raw, showdown_capped_source_pp: move.pp,
    showdown_battle_max_pp: slot.maxpp, observed_remaining_pp: remaining,
    source_layer_match: move.pp === capped};
  const out = [result("pp.battle_max", slot.maxpp, battleMax, ev, [], details)];
  if (observedBase !== undefined) out.push(result("pp.base", move.pp, observedBase, ev, [], details));
  return out;
}
function damageCheck(check, ev) {
  const missing = [];
  const attack = mon(check.attacker, ev, missing);
  const defend = mon(check.defender, ev, missing);
  const moveId = fstr(check, "move_id", ev, missing);
  const hpBefore = fnum(check, "defender_hp_before", ev, missing);
  const hpAfter = fnum(check, "defender_hp_after", ev, missing);
  const critical = fact(check.critical, "critical", ev, missing, "boolean");
  const isolated = fact(check.direct_hit_only, "direct_hit_only", ev, missing, "boolean");
  const complete = fact(check.conditions_complete, "conditions_complete", ev, missing, "boolean");
  const substitute = fact(check.defender_substitute, "defender_substitute", ev, missing, "boolean");
  const endure = fact(check.defender_endure, "defender_endure", ev, missing, "boolean");
  const weather = fstr(check.field, "weather", ev, missing);
  const terrain = fstr(check.field, "terrain", ev, missing);
  const screen = fstr(check.field, "defender_screen", ev, missing);
  const status = [], boosts = [];
  for (const side of ["attacker", "defender"]) {
    status.push(fstr(check[side], "status", ev, missing));
    const values = {};
    for (const s of STATS.slice(1)) values[s] = fact(check[side]?.boosts?.[s], side + ".boosts." + s, ev, missing, "number");
    boosts.push(values);
  }
  if (status.some(s => s !== undefined && !STATUS_IDS.has(s)))
    missing.push("status must use a pinned canonical ID (brn/par/psn/tox/slp/frz or empty)");
  const move = moveId && dex.moves.get(moveId);
  if (moveId && (!move.exists || move.category === "Status" || !move.basePower || move.basePowerCallback ||
    move.multihit || move.damage || move.damageCallback || move.ohko)) missing.push("unsupported damage move");
  if (move?.exists && postDamageEffect(move)) missing.push("move applies after raw getDamage");
  if (move?.exists && attack) legalMove(attack.species, move.id, missing);
  if (!isolated) missing.push("HP change is not verified as one direct hit");
  if (!complete) missing.push("attacker, defender and field conditions are not verified complete");
  if (substitute || endure) missing.push("substitute or endure changes final HP loss");
  if (defend && (postDamageEffect(dex.abilities.get(defend.ability)) || postDamageEffect(dex.items.get(defend.item))))
    missing.push("defender ability/item applies after raw getDamage");
  const weatherEffect = weather !== undefined && weather !== "" ? dex.conditions.get(weather) : null;
  if (terrain || !WEATHER_IDS.has(weather) ||
    (weatherEffect && (!weatherEffect.exists || weatherEffect.id !== weather || weatherEffect.effectType !== "Weather")) ||
    !["", "reflect", "lightscreen"].includes(screen)) missing.push("unsupported field condition");
  if (hpBefore !== undefined && hpAfter !== undefined && (hpAfter > hpBefore || hpBefore < 1 || hpAfter < 0))
    missing.push("invalid HP interval");
  if (missing.length) return result("damage", null, null, ev, missing);
  attack.moves = [move.id];
  const b = battle(attack, defend);
  const a = b.p1.active[0], d = b.p2.active[0];
  if (a.ability !== dex.abilities.get(attack.ability).id ||
      d.ability !== dex.abilities.get(defend.ability).id ||
      a.item !== dex.items.get(attack.item).id || d.item !== dex.items.get(defend.item).id)
    return result("damage", null, null, ev, ["battle initialization changed observed ability/item"]);
  if (hpBefore > d.maxhp) return result("damage", null, null, ev, ["observed HP exceeds Showdown max HP"]);
  d.hp = hpBefore;
  for (const [p, index] of [[a, 0], [d, 1]]) {
    p.status = status[index];
    for (const s of STATS.slice(1)) {
      if (boosts[index][s] < -6 || boosts[index][s] > 6) return result("damage", null, null, ev, ["boost outside range"]);
      p.boosts[s] = boosts[index][s];
    }
  }
  if (b.field.weather && !b.field.clearWeather())
    return result("damage", null, null, ev, ["initial weather could not be cleared"]);
  if (weather && !b.field.setWeather(weather, a))
    return result("damage", null, null, ev, ["requested weather was not applied"]);
  if (b.field.weather !== weather)
    return result("damage", null, null, ev, ["applied weather differs from observed canonical ID"]);
  if (screen) b.p2.addSideCondition(screen, d);
  const rolls = [];
  for (let factor = 85; factor <= 100; factor++) {
    b.randomizer = base => b.trunc(b.trunc(base * factor) / 100);
    const activeMove = b.dex.getActiveMove(move.id);
    activeMove.willCrit = critical;
    const damage = b.actions.getDamage(a, d, activeMove, true);
    if (!Number.isInteger(damage)) return result("damage", null, null, ev, ["damage calculation returned noninteger"]);
    rolls.push(Math.min(damage, hpBefore));
  }
  const expected = [Math.min(...rolls), Math.max(...rolls)];
  return result("damage", expected, hpBefore - hpAfter, ev, [], {move_id: move.id, rolls});
}
function postDamageEffect(effect) {
  return !!(effect?.onDamage || effect?.onTryHit);
}
function compareCase(c) {
  if (!c || typeof c.case_id !== "string" || !c.case_id ||
      !Number.isFinite(Date.parse(c.observed_at)) || c.regulation !== "M-C" ||
      !c.evidence || !["REAL_GAME", "SYNTHETIC"].includes(c.evidence.origin) ||
      !["USER_VERIFIED", "UNVERIFIED"].includes(c.evidence.verification) ||
      !Array.isArray(c.evidence.references) || !c.checks || typeof c.checks !== "object")
    throw Error("invalid case metadata/evidence: " + (c?.case_id || "?"));
  if (c.evidence.origin === "SYNTHETIC" && c.evidence.verification === "USER_VERIFIED")
    throw Error("synthetic case cannot be user verified");
  const checks = [];
  if (c.checks.stats) checks.push(...[].concat(statsCheck(c.checks.stats, c.evidence)));
  if (c.checks.pp) checks.push(...[].concat(ppCheck(c.checks.pp, c.evidence)));
  if (c.checks.damage) checks.push(...[].concat(damageCheck(c.checks.damage, c.evidence)));
  if (!checks.length) throw Error("case has no checks: " + c.case_id);
  return {case_id: c.case_id, origin: c.evidence.origin, verification: c.evidence.verification, checks};
}
function run(file) {
  assertPinned();
  const input = fs.readFileSync(file);
  const doc = JSON.parse(input.toString("utf8"));
  validateShape(doc, schema);
  if (doc.schema !== "champions-real-game-golden-v1" || !Array.isArray(doc.cases))
    throw Error("invalid golden document schema");
  const ids = new Set();
  const cases = doc.cases.map(c => {
    if (ids.has(c.case_id)) throw Error("duplicate case ID " + c.case_id);
    ids.add(c.case_id);
    return compareCase(c);
  });
  const all = cases.flatMap(c => c.checks);
  const verifiedCases = cases.filter(c => c.origin === "REAL_GAME" && c.verification === "USER_VERIFIED");
  const verifiedChecks = verifiedCases.flatMap(c => c.checks).filter(x => x.status === "MATCH" || x.status === "MISMATCH");
  return {schema: "champions-golden-comparison-v2", pinned_sha: PIN, format: FORMAT,
    input_sha256: sha256(input), master_sha256: sources().master_sha256,
    gate_A_calculation: "UNKNOWN", scope: "OFFLINE_DIAGNOSTIC_ONLY; NOT_GATE_A_CERTIFICATION", cases,
    counts: {match: all.filter(x => x.status.startsWith("MATCH")).length,
      mismatch: all.filter(x => x.status.startsWith("MISMATCH")).length,
      verified_game_case_count: verifiedCases.length,
      verified_game_check_count: verifiedChecks.length,
      verified_game_match_check_count: verifiedChecks.filter(x => x.status === "MATCH").length,
      verified_game_matching_case_count: verifiedCases.filter(c => c.checks.every(x => x.status === "MATCH")).length,
      inconclusive: all.filter(x => x.status === "INCONCLUSIVE").length,
      synthetic: all.filter(x => x.status.endsWith("_SYNTHETIC")).length,
      unverified: all.filter(x => x.status.endsWith("_UNVERIFIED")).length}};
}
if (require.main === module) {
  try {
    if (process.argv.length !== 3) throw Error("Usage: node golden_compare.cjs CASES.json");
    const report = run(path.resolve(process.argv[2]));
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    if (report.counts.mismatch) process.exitCode = 1;
  } catch (e) {
    process.stdout.write(JSON.stringify({status: "NOT_RUN", reason: e.message}) + "\n");
    process.exitCode = 2;
  }
}
module.exports = {run, compareCase, assertPinned, postDamageEffect};
