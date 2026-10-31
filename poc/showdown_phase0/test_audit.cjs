"use strict";
// Runs inside the pinned Showdown PoC container only. These are static/source
// regression checks and are NOT Champions in-game golden measurements.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {rows, compare, speciesKeyForRow, cappedSourcePP, calculatedBattleMaxPP} = require("./audit.cjs");
const fs = require("node:fs");
const path = require("node:path");

test("form comparison resolves battle species, not shared learnset key", () => {
  const cases = [
    ["パンプジン", "おおきいサイズ", "gourgeist", "gourgeistlarge"],
    ["パンプジン", "ちいさいサイズ", "gourgeist", "gourgeistsmall"],
    ["パンプジン", "とくだいサイズ", "gourgeist", "gourgeistsuper"],
    ["パンプジン", "ふつうのサイズ", "gourgeist", "gourgeist"],
    ["ポワルン", "あまみずのすがた", "castform", "castformrainy"],
    ["ポワルン", "たいようのすがた", "castform", "castformsunny"],
    ["ポワルン", "ゆきぐものすがた", "castform", "castformsnowy"],
    ["ギルガルド", "シールドフォルム", "aegislash", "aegislash"],
    ["ギルガルド", "ブレードフォルム", "aegislash", "aegislashblade"],
    ["イルカマン", "ナイーブフォルム", "palafin", "palafin"],
    ["イルカマン", "マイティフォルム", "palafin", "palafinhero"],
  ];
  for (const [pokemon, form, learnset, species] of cases) {
    assert.equal(speciesKeyForRow({"ポケモン名": pokemon, "フォーム/姿": form}, learnset), species,
      pokemon + ":" + form);
  }
  assert.equal(speciesKeyForRow({"ポケモン名": "メガニウム", "フォーム/姿": ""}, "meganium"), "meganium");
  assert.equal(speciesKeyForRow({"ポケモン名": "unknown", "フォーム/姿": ""}, "unknown"), "unknown");
});

test("Champions source PP cap is not battle max PP", () => {
  for (const [raw, capped, battleMax] of [
    [5, 5, 8], [10, 10, 12], [15, 15, 16],
    [20, 20, 20], [25, 20, 20], [40, 20, 20],
  ]) {
    assert.equal(cappedSourcePP(raw), capped);
    assert.equal(calculatedBattleMaxPP({pp: capped}), battleMax);
  }
  assert.equal(calculatedBattleMaxPP({pp: 5, noPPBoosts: true}), 5);
});

test("normal and Mega Meganium are distinct in the source master", () => {
  const filename = path.resolve(__dirname, "masters", "Pokemon_Champions_MC_usable_pokemon.csv");
  const masters = rows(fs.readFileSync(filename, "utf8"));
  const original = masters.find(x => x["ポケモン名"] === "メガニウム" && x["区分"] === "通常");
  const mega = masters.find(x => x["ポケモン名"] === "メガメガニウム" && x["区分"] === "メガシンカ");
  assert.ok(original);
  assert.ok(mega);
  assert.equal(original["タイプ1"], "くさ");
  assert.equal(original["タイプ2"], "");
  assert.equal(original["特性"], "しんりょく / リーフガード");
  assert.deepEqual(["H", "A", "B", "C", "D", "S"].map(k => Number(original[k])),
    [80, 82, 100, 83, 100, 80]);
  assert.equal(Number(original["種族値合計"]), 525);
  assert.equal(mega["タイプ2"], "フェアリー");
  assert.equal(mega["特性"], "メガソーラー");
  assert.deepEqual(["H", "A", "B", "C", "D", "S"].map(k => Number(mega[k])),
    [80, 92, 115, 143, 115, 80]);
});

test("fixed-source audit accounts for forms and PP cap yet keeps unverified Steelix discrepancy", () => {
  const audit = compare();
  assert.equal(audit.status, "DIAGNOSTIC_ONLY");
  assert.equal(audit.totals.move_pp.checked, 510);
  assert.equal(audit.totals.move_pp.mismatched, 0);
  assert.equal(audit.source_pp_cap_case_count, 33);
  assert.equal(audit.totals.species_base_stats.mismatched, 0);
  assert.equal(audit.totals.species_types.mismatched, 0);
  assert.equal(audit.explicit_form_comparisons.length, 8);
  assert.equal(audit.totals.learnset_membership.mismatched, 1);
  assert.equal(audit.mismatch_count, 1);
  assert.equal(audit.mismatch_details[0].group, "learnset_membership");
  assert.equal(audit.mismatch_details[0].key, "steelix");
  assert.equal(audit.mismatch_details[0].field, "raindance");
  assert.equal(audit.mismatch_details[0].validation_status, "UNKNOWN");
  assert.equal(audit.classification_status, "UNVERIFIED_HYPOTHESES; NO_CHAMPIONS_GOLDEN");
});
