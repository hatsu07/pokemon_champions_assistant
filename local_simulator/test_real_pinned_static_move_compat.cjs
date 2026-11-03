"use strict";
/* Exact pinned Showdown Champions Dex vs source-derived 6x24 fixed move fields.
 * Results are intentionally metadata-only, never game legality/AI rankings. */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const {comparePinnedChampionsStaticMoves} =
  require("./pinned_move_static_comparison.cjs");
const {PINNED_UPSTREAM_SHA} =
  require("./pinned_champions_roster_lookup.cjs");

const upstream = process.env.SHOWDOWN_PINNED_BUILD_ROOT;
const reportFile = process.env.CHAMPIONS_STATIC_MOVES_JSON;
assert.ok(upstream && reportFile, "Actual pinned engine and Champions source JSON required");
assert.equal(cp.execFileSync("git", ["-C",upstream,"rev-parse","HEAD"],
                            {encoding:"utf8"}).trim(), PINNED_UPSTREAM_SHA);
const {Dex} = require(path.join(upstream,"dist","sim","dex.js"));
const dex = Dex.mod("champions");
assert.equal(dex.currentMod,"champions");

test("registered 24 static fields are compared with *actual* pinned Dex",()=>{
  const raw = fs.readFileSync(reportFile);
  assert.ok(raw.length > 100 && raw.length < 300_000);
  const source = JSON.parse(raw.toString("utf8"));
  assert.equal(source.status,"CHAMPIONS_STATIC_24_SOURCE_BOUND");
  const result = comparePinnedChampionsStaticMoves(source,dex);
  assert.equal(result.checked_slots,24,JSON.stringify(result));
  assert.equal(result.ranking_status,"NO_RANKING");
  assert.equal(result.ready_for_live,false);
  assert.equal(result.game_mechanics_verified,false);
  assert.ok(["PINNED_STATIC_FIELDS_EQUAL_UNATTESTED",
             "PINNED_STATIC_FIELDS_DIFFER",
             "PINNED_STATIC_IDS_MISSING"].includes(result.status));
  console.log("Actual static Champions-master vs pinned Dex metadata:",
              JSON.stringify(result));
});
