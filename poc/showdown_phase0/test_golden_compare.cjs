"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {compareCase, assertPinned, run, postDamageEffect} = require("./golden_compare.cjs");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const crypto = require("node:crypto");
const f = value => ({source: "SYNTHETIC", value});
const ap = Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(0)]));
const boosts = Object.fromEntries(["atk", "def", "spa", "spd", "spe"].map(k => [k, f(0)]));
const p = (species, ability) => ({species: f(species), level: f(50), nature: f("Serious"),
  ap: structuredClone(ap), ability: f(ability), item: f(""), status: f(""), boosts: structuredClone(boosts)});
const base = () => ({case_id: "SYN-GOLDEN-1", observed_at: "2026-10-31T00:00:00Z",
  regulation: "M-C", evidence: {origin: "SYNTHETIC", verification: "UNVERIFIED", references: []},
  checks: {}});
test("fixed SHA and synthetic stat / PP comparisons", () => {
  assertPinned();
  const c = base();
  c.checks.stats = {pokemon: p("Garchomp", "Rough Skin"), observed_stats:
    Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(1)]))};
  c.checks.pp = {pokemon: {species: f("Beedrill")}, move_id: f("Agility"), observed_battle_max_pp: f(20),
    observed_remaining_pp: {source: "UNKNOWN"}};
  const results = compareCase(c).checks;
  assert.equal(results.length, 7);
  assert.ok(results.slice(0, 6).every(r => r.status === "MISMATCH_SYNTHETIC"));
  assert.deepEqual(results.slice(0, 6).map(r => r.showdown), [183, 150, 115, 100, 105, 122]);
  assert.equal(results[6].status, "MATCH_SYNTHETIC");
  assert.equal(results[6].master_raw_pp, 30);
  assert.equal(results[6].showdown_capped_source_pp, 20);
  assert.equal(results[6].showdown_battle_max_pp, 20);
});
test("damage uses known conditions and 16 pinned-engine rolls", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"),
    defender: p("Hippowdon", "Sand Stream"), move_id: f("Earthquake"),
    defender_hp_before: f(100), defender_hp_after: f(70),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    defender_substitute: f(false), defender_endure: f(false),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  const r = compareCase(c).checks[0];
  assert.equal(r.kind, "damage");
  assert.equal(r.rolls.length, 16);
  assert.ok(r.showdown[0] <= r.showdown[1]);
  c.checks.damage.defender_hp_after = f(100 - r.showdown[0]);
  assert.equal(compareCase(c).checks[0].status, "MATCH_SYNTHETIC");
  c.checks.damage.defender_hp_after = f(100);
  assert.equal(compareCase(c).checks[0].status, "MISMATCH_SYNTHETIC");
  c.checks.damage.field.defender_screen = f("reflect");
  const screened = compareCase(c).checks[0];
  assert.ok(screened.showdown[1] < r.showdown[1]);
  c.checks.damage.conditions_complete = {source: "UNKNOWN"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
});
test("nature modifies the displayed stat under the pinned Champions formula", () => {
  const c = base();
  c.checks.stats = {pokemon: p("Garchomp", "Rough Skin"), observed_stats:
    Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(1)]))};
  c.checks.stats.pokemon.nature = f("Jolly");
  const got = compareCase(c).checks;
  assert.equal(got.find(r => r.kind === "stats.spa").showdown, 90);
  assert.equal(got.find(r => r.kind === "stats.spe").showdown, 134);
});
test("unknown or inferred private inputs never become a match", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"),
    defender: p("Hippowdon", "Sand Stream"), move_id: f("Earthquake"),
    defender_hp_before: f(100), defender_hp_after: f(70),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    defender_substitute: f(false), defender_endure: f(false),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  c.checks.damage.defender.item = {source: "UNKNOWN"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.damage.defender.item = {source: "INFERRED", value: "Leftovers"};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.damage.defender.item = f("");
  c.checks.damage.field.terrain = f("electricterrain");
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
});
test("real-game facts require independent evidence references", () => {
  const c = base();
  c.evidence = {origin: "REAL_GAME", verification: "USER_VERIFIED",
    references: [{id: "frame-1", description: "local verified screenshot"}]};
  c.checks.pp = {pokemon: {species: f("Beedrill")}, move_id: f("Agility"), observed_battle_max_pp: f(20)};
  assert.equal(compareCase(c).checks[0].status, "INCONCLUSIVE");
  c.checks.pp = {pokemon: {species: {source: "GAME_OBSERVED", value: "Beedrill", evidence_ids: ["frame-1"]}},
    move_id: {source: "GAME_OBSERVED", value: "Agility", evidence_ids: ["frame-1"]},
    observed_battle_max_pp: {source: "GAME_OBSERVED", value: 20, evidence_ids: ["frame-1"]}};
  assert.equal(compareCase(c).checks[0].status, "MATCH");
});
test("CLI report keeps synthetic evidence outside verified game matches", () => {
  const report = run(path.join(__dirname, "synthetic_golden_example.json"));
  assert.equal(report.schema, "champions-golden-comparison-v2");
  assert.equal(report.gate_A_calculation, "UNKNOWN");
  assert.equal(report.input_sha256, crypto.createHash("sha256").update(
    fs.readFileSync(path.join(__dirname, "synthetic_golden_example.json"))).digest("hex"));
  assert.equal(Object.keys(report.master_sha256).length, 3);
  for (const [name, hash] of Object.entries(report.master_sha256)) {
    assert.equal(hash, crypto.createHash("sha256").update(fs.readFileSync(path.join(__dirname, "masters", name))).digest("hex"));
  }
  assert.deepEqual(run(path.join(__dirname, "synthetic_golden_example.json")), report);
  assert.deepEqual(report.counts, {match: 1, mismatch: 0, verified_game_case_count: 0,
    verified_game_check_count: 0, verified_game_match_check_count: 0, verified_game_matching_case_count: 0,
    inconclusive: 0, synthetic: 1, unverified: 0});
  const c = base();
  c.evidence.verification = "USER_VERIFIED";
  c.checks.pp = {pokemon: {species: f("Beedrill")}, move_id: f("Agility"), observed_battle_max_pp: f(20)};
  assert.throws(() => compareCase(c), /synthetic case cannot be user verified/);
});
test("ranked level and Stat Point limits reject impossible sets", () => {
  const c = base();
  c.checks.stats = {pokemon: p("Garchomp", "Rough Skin"), observed_stats:
    Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, f(1)]))};
  const mon = c.checks.stats.pokemon;
  mon.ap.atk = f(100);
  assert.match(compareCase(c).checks[0].missing.join(" "), /0\.\.32/);
  mon.ap.atk = f(32); mon.ap.spe = f(32); mon.ap.hp = f(3);
  assert.match(compareCase(c).checks[0].missing.join(" "), /exceeds 66/);
  mon.ap.hp = f(2); mon.level = f(51);
  assert.match(compareCase(c).checks[0].missing.join(" "), /level 50/);
  mon.level = f(50);
  assert.equal(compareCase(c).checks.length, 6);
});
test("Champions species, form, ability and move must be legal", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"), defender: p("Hippowdon", "Sand Stream"),
    move_id: f("Earthquake"), defender_hp_before: f(100), defender_hp_after: f(70),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    defender_substitute: f(false), defender_endure: f(false),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  c.checks.damage.defender.ability = f("Rough Skin");
  assert.match(compareCase(c).checks[0].missing.join(" "), /ability not legal/);
  c.checks.damage.defender.ability = f("Sand Stream");
  c.checks.damage.defender.form = f("Mega");
  assert.match(compareCase(c).checks[0].missing.join(" "), /form contradicts/);
  c.checks.damage.defender.form = f("");
  c.checks.damage.move_id = f("Agility");
  assert.match(compareCase(c).checks[0].missing.join(" "), /move not in Champions master/);
  c.checks.pp = {pokemon: {species: f("Beedrill")}, move_id: f("Earthquake"), observed_battle_max_pp: f(12)};
  assert.match(compareCase(c).checks.find(x => x.kind === "pp").missing.join(" "), /move not in Champions master/);
});
test("Focus Sash, Sturdy, Disguise, and Substitute cannot be compared with raw getDamage", () => {
  const c = base();
  c.checks.damage = {attacker: p("Garchomp", "Rough Skin"), defender: p("Hippowdon", "Sand Stream"),
    move_id: f("Earthquake"), defender_hp_before: f(183), defender_hp_after: f(1),
    critical: f(false), direct_hit_only: f(true), conditions_complete: f(true),
    defender_substitute: f(false), defender_endure: f(false),
    field: {weather: f(""), terrain: f(""), defender_screen: f("")}};
  c.checks.damage.attacker.boosts.atk = f(6);
  const rawSashCase = compareCase(c).checks[0];
  assert.deepEqual(rawSashCase.showdown, [183, 183], "raw damage would KO at full HP");
  c.checks.damage.defender.item = f("Focus Sash");
  assert.match(compareCase(c).checks[0].missing.join(" "), /after raw getDamage/);
  c.checks.damage.defender.item = f("");
  c.checks.damage.defender = p("Steelix", "Sturdy");
  c.checks.damage.defender_hp_before = f(150);
  assert.match(compareCase(c).checks[0].missing.join(" "), /after raw getDamage/);
  c.checks.damage.defender.ability = f("Sheer Force");
  assert.deepEqual(compareCase(c).checks[0].showdown, [150, 150], "raw damage would KO Sturdy user");
  c.checks.damage.defender.ability = f("Sturdy");
  c.checks.damage.defender = p("Mimikyu", "Disguise");
  c.checks.damage.defender_hp_before = f(130);
  assert.match(compareCase(c).checks[0].missing.join(" "), /after raw getDamage/);
  c.checks.damage.defender = p("Hippowdon", "Sand Stream");
  c.checks.damage.defender_hp_before = f(183);
  c.checks.damage.defender_substitute = f(true);
  assert.match(compareCase(c).checks[0].missing.join(" "), /substitute or endure/);
  c.checks.damage.defender_substitute = f(false);
  c.checks.damage.defender_endure = f(true);
  assert.match(compareCase(c).checks[0].missing.join(" "), /substitute or endure/);
  const {Dex} = require(path.join(process.env.SHOWDOWN_ROOT, "dist", "sim", "index.js"));
  assert.equal(postDamageEffect(Dex.forFormat("gen9championsbssregmc").abilities.get("Ice Face")), true);
});
test("one verified stats case has six verified checks, and gate A stays UNKNOWN", () => {
  // Synthetic unit fixture for the counting branch; not an independent game observation.
  const g = value => ({source: "GAME_OBSERVED", value, evidence_ids: ["frame-1"]});
  const c = base();
  c.case_id = "REAL-1";
  c.evidence = {origin: "REAL_GAME", verification: "USER_VERIFIED",
    references: [{id: "frame-1", description: "test-only synthetic reference"}]};
  c.checks.stats = {pokemon: {species: g("Garchomp"), level: g(50), nature: g("Serious"),
    ap: Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map(k => [k, g(0)]))},
  observed_stats: Object.fromEntries(["hp", "atk", "def", "spa", "spd", "spe"].map((k, i) =>
    [k, g([183, 150, 115, 100, 105, 122][i])]))};
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "phase0-golden-test-"));
  try {
    const file = path.join(dir, "cases.json");
    fs.writeFileSync(file, JSON.stringify({schema: "champions-real-game-golden-v1", cases: [c]}));
    const report = run(file);
    assert.equal(report.counts.verified_game_case_count, 1);
    assert.equal(report.counts.verified_game_check_count, 6);
    assert.equal(report.counts.verified_game_match_check_count, 6);
    assert.equal(report.counts.verified_game_matching_case_count, 1);
    assert.equal(report.gate_A_calculation, "UNKNOWN");
  } finally {fs.rmSync(dir, {recursive: true, force: true});}
});
