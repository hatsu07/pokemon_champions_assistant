"use strict";
/**
 * Real pinned-upstream metadata-only test for actual Champions six/24
 * source-key candidates. We DO NOT construct or reconstruct a game Battle.
 * A Dex entry being present neither proves it is usable in Champions nor
 * resolves the registered "Mega Lucario Z" form. Never run this in LIVE.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const {auditPinnedChampionsRosterKeys, PINNED_UPSTREAM_SHA} =
  require("./pinned_champions_roster_lookup.cjs");

const root = path.resolve(__dirname, "..");
const upstream = process.env.SHOWDOWN_PINNED_BUILD_ROOT;
const inventory = process.env.CHAMPIONS_SOURCE_KEY_CANDIDATES_JSON;
assert.ok(upstream, "Real pinned Showdown root is required, never a fake engine");
assert.ok(inventory, "Source-produced Python candidate JSON path required");
const git = cp.execFileSync("git", ["-C", upstream, "rev-parse", "HEAD"],
                            {encoding:"utf8"}).trim();
assert.equal(git, PINNED_UPSTREAM_SHA);
const {Dex} = require(path.join(upstream, "dist", "sim", "dex.js"));
assert.ok(Dex && typeof Dex.mod === "function");
const dex = Dex.mod("champions");
assert.equal(dex.currentMod, "champions");

test("actual registered six/24 candidate IDs resolve in pinned Champions Dex only", () => {
  const raw = fs.readFileSync(inventory);
  assert.ok(raw.length > 100 && raw.length < 400_000);
  const sources = JSON.parse(raw.toString("utf8"));
  assert.equal(sources.status, "CHAMPIONS_KEY_CANDIDATES_ONLY");
  const report = auditPinnedChampionsRosterKeys(sources, root, dex);
  assert.equal(report.source_hashes_verified, true, report.reason);
  assert.equal(report.pinned_upstream_sha, PINNED_UPSTREAM_SHA);
  assert.deepEqual(report.unsupported_forms, ["ルカリオ → メガルカリオZ"]);
  assert.equal(report.mapping_independently_attested, false);
  assert.equal(report.champions_mechanics_verified, false);
  assert.equal(report.executable_in_live, false);
  assert.equal(report.ranking_status, "NO_RANKING");
  // This is a verification target; a true lack of Dex entries should FAIL
  // the gate and be reported, never skipped or replaced with SV IDs.
  assert.equal(report.status, "PINNED_ENGINE_KEYS_EXIST_UNATTESTED",
    "Missing exact pinned Champions Dex entries: " + JSON.stringify(report));
  assert.equal(report.species_found, 6);
  assert.equal(report.moves_found, 24);
  console.log("Pinned Showdown source-key existence only:",
              JSON.stringify(report));
});
