"use strict";
// Static source-to-source comparison only; NOT proof of in-game damage compatibility.
const fs = require("node:fs");
const path = require("node:path");
const root = process.env.SHOWDOWN_ROOT;
if (!root) throw new Error("SHOWDOWN_ROOT required");
const {Dex} = require(path.join(root, "dist", "sim", "index.js"));
const dex = Dex.forFormat("gen9championsbssregmc");
const base = path.join(__dirname, "masters");

const types = {
  "ノーマル": "Normal", "ほのお": "Fire", "みず": "Water", "でんき": "Electric",
  "くさ": "Grass", "こおり": "Ice", "かくとう": "Fighting", "どく": "Poison",
  "じめん": "Ground", "ひこう": "Flying", "エスパー": "Psychic", "むし": "Bug",
  "いわ": "Rock", "ゴースト": "Ghost", "ドラゴン": "Dragon", "あく": "Dark",
  "はがね": "Steel", "フェアリー": "Fairy",
};
const categories = {"物理": "Physical", "特殊": "Special", "変化": "Status"};
// Handles quoted commas, CRLF and doubled quotes; refuses malformed source CSV.
function rows(csv) {
  let result = [], row = [], field = "", quoted = false;
  const data = csv.replace(/^\uFEFF/, "");
  for (let i = 0; i < data.length; i++) {
    const c = data[i];
    if (c === '"') {
      if (quoted && data[i + 1] === '"') {field += '"'; i++;}
      else quoted = !quoted;
    } else if (c === "," && !quoted) {
      row.push(field); field = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && data[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some(v => v !== "")) result.push(row);
      row = [];
    } else field += c;
  }
  if (quoted) throw new Error("unterminated CSV quote");
  if (field || row.length) {row.push(field); result.push(row);}
  const headers = result.shift();
  if (!headers) throw new Error("empty CSV");
  return result.map((arr, rowIndex) => {
    if (arr.length !== headers.length) throw new Error("CSV column mismatch at " + (rowIndex + 2));
    return Object.fromEntries(headers.map((key, i) => [key, arr[i]]));
  });
}
function load(name) {return rows(fs.readFileSync(path.join(base, name), "utf8"));}
function compare() {
  const moves = load("Pokemon_Champions_MC_move_master.csv");
  const pokemon = load("Pokemon_Champions_MC_usable_pokemon.csv");
  const links = load("Pokemon_Champions_MC_pokemon_learnsets_linked.csv");
  const items = load("Pokemon_Champions_MC_usable_held_items.csv");
  const discrepancies = [];
  const totals = {};
  function check(group, key, field, expected, actual) {
    totals[group] ??= {checked: 0, matching: 0, mismatched: 0, missing: 0};
    const t = totals[group]; t.checked++;
    if (actual === undefined || actual === null) {t.missing++; discrepancies.push({group, key, field, expected, actual: null});}
    else if (String(actual) !== String(expected)) {
      t.mismatched++; discrepancies.push({group, key, field, expected, actual});
    } else t.matching++;
  }
  for (const row of moves) {
    const id = row.MoveKey, got = dex.moves.get(id);
    check("move_coverage", id, "exists", true, !!got.exists);
    if (!got.exists) continue;
    if (types[row["タイプ"]]) check("move_type", id, "type", types[row["タイプ"]], got.type);
    if (categories[row["分類"]]) check("move_category", id, "category", categories[row["分類"]], got.category);
    for (const [column, field, group] of [["威力", "basePower", "move_power"],
      ["PP", "pp", "move_pp"], ["優先度", "priority", "move_priority"]]) {
      if (/^-?\d+$/.test(row[column])) check(group, id, field, Number(row[column]), got[field]);
    }
    if (row["命中"] === "必中") check("move_accuracy", id, "accuracy", true, got.accuracy);
    else if (/^\d+$/.test(row["命中"])) check("move_accuracy", id, "accuracy", Number(row["命中"]), got.accuracy);
  }
  const linked = new Map(links.map(x => [x["ポケモン名"] + "|" + x["フォーム/姿"], x.LearnsetKey]));
  for (const row of pokemon) {
    // Compare only directly registrable non-Mega bodies, not derived forme inheritance.
    if (row["区分"] === "メガシンカ") continue;
    const key = linked.get(row["ポケモン名"] + "|" + row["フォーム/姿"]);
    if (!key) {check("pokemon_link", row["ポケモン名"], "learnsetKey", "present", undefined);continue;}
    const mon = dex.species.get(key);
    check("species_coverage", key, "exists", true, !!mon.exists);
    if (!mon.exists) continue;
    const columnToStat = {H:"hp", A:"atk", B:"def", C:"spa", D:"spd", S:"spe"};
    for (const [col, stat] of Object.entries(columnToStat)) {
      if (/^\d+$/.test(row[col])) check("species_base_stats", key, stat, Number(row[col]), mon.baseStats[stat]);
    }
    const expectedTypes = [row["タイプ1"], row["タイプ2"]].filter(Boolean).map(x=>types[x] || "?");
    if (!expectedTypes.includes("?")) {
      check("species_types", key, "types", expectedTypes.join("/"), mon.types.join("/"));
    }
  }
  const uniqueLinks = new Map(links.map(x => [x.LearnsetKey, x]));
  for (const [key, row] of uniqueLinks) {
    const got = dex.learnsets.get(key);
    const expected = (row["習得MoveKey一覧"] || "").split(",").map(x=>x.trim()).filter(Boolean);
    const known = new Set(Object.keys(got.learnset || {}));
    for (const id of expected) check("learnset_membership", key, id, true, known.has(id));
  }
  for (const row of items) {
    const name = row["英語名"];
    if (name) check("item_coverage", name, "exists", true, !!dex.items.get(name).exists);
  }
  return {source: "Champions fixed masters versus pinned Showdown mod, static comparison",
    format: "gen9championsbssregmc", upstream_sha: "ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
    totals, mismatch_count: discrepancies.length,
    mismatch_examples: discrepancies.slice(0, 100),
    caveats: [
      "Values can disagree due to source update timing; this is a diagnostic, not game truth.",
      "Direct source agreement is not an independent real-game golden case.",
      "Effects/ability semantics and damage sequences are not proved by this audit.",
      "Mega/forms and known aliases need independent observational validation.",
    ]};
}
if (require.main === module) {
  const result = compare();
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
}
module.exports = {rows, compare};
