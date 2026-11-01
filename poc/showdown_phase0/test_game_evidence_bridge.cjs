"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {assertEvidenceProvenance, summarizeGolden, summarizePartial,
  createReport, run, PARTIAL_FIELDS} = require("./game_evidence_bridge.cjs");
const {context} = require("./six_team_selection_probe.cjs");
const {assertObservation} = require("./partial_observation_probe.cjs");
const {run: goldenRun} = require("./golden_compare.cjs");
const file = path.join(__dirname, "synthetic_golden_example.json");
const input = fs.readFileSync(file), doc = JSON.parse(input);

const os = require("node:os");
const {execFileSync} = require("node:child_process");

function withTempFiles(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "champions-private-evidence-stub-"));
  try { return fn(dir); } finally { fs.rmSync(dir, {recursive: true, force: true}); }
}
function structuralPartialStub() {
  const facts = Object.fromEntries(PARTIAL_FIELDS.map(k => [k, {source:"UNKNOWN"}]));
  const secretRef = "STRUCTURAL_STUB_NOT_REAL_GAME_SENSITIVE_REFERENCE_987";
  facts.opponent_hp_exact = {
    source:"GAME_OBSERVED", value:53, evidence_ids:[secretRef]
  };
  facts.weather = {source:"INFERRED", value:"raindance",
    reason:"STRUCTURAL_STUB_NOT_REAL_GAME_PREDICTION_ONLY"};
  return {schema:"phase0-partial-observation-v1",
    origin:"REAL_GAME", verification:"USER_VERIFIED",
    evidence:[{id:secretRef,kind:"GAME_FRAME"}], facts};
}
function runCli(args) {
  try {
    return {code:0, output:execFileSync(process.execPath,
      [path.join(__dirname,"game_evidence_bridge.cjs"),...args],
      {encoding:"utf8",stdio:["ignore","pipe","pipe"]})};
  } catch (error) {
    return {code:error.status,output:error.stdout?.toString("utf8") ?? "",
      stderr:error.stderr?.toString("utf8") ?? ""};
  }
}


test("existing synthetic Golden is audited without claiming game observations", () => {
  const actual = run({goldenPath: file});
  assert.equal(actual.schema, "phase0-local-evidence-boundary-v1");
  assert.deepEqual([actual.gate_A, actual.gate_B, actual.adoption],
    ["UNKNOWN", "UNSUPPORTED", "NO_GO"]);
  assert.equal(actual.golden_scalar.origin_counts.REAL_GAME, 0);
  assert.equal(actual.golden_scalar.comparison_counts.synthetic, 1);
  assert.equal(actual.golden_scalar.comparison_counts.verified_game_check_count, 0);
  assert.equal(actual.golden_scalar.fact_source_counts.GAME_OBSERVED, 0);
  assert.equal(actual.golden_scalar.b_midbattle_fields_transferred, 0);
  assert.equal(actual.B_full_snapshot_created, false);
  assert.equal(actual.game_golden_promoted_to_B, false);
  assert.equal(actual.partial_board, null);
  assert.equal(actual.golden_scalar.input_sha256,
    crypto.createHash("sha256").update(input).digest("hex"));
});
test("source fraud, unlinked evidence, unknown values and repeated case IDs reject", () => {
  const clone = () => structuredClone(doc);
  const fakeGame = clone();
  fakeGame.cases[0].checks.pp.move_id.source = "GAME_OBSERVED";
  assert.throws(() => assertEvidenceProvenance(fakeGame), /GAME_OBSERVED/);
  const fakeVal = clone();
  fakeVal.cases[0].checks.pp.observed_remaining_pp.value = 20;
  assert.throws(() => assertEvidenceProvenance(fakeVal), /UNKNOWN carries/);
  const repeated = clone();
  repeated.cases.push(structuredClone(repeated.cases[0]));
  assert.throws(() => assertEvidenceProvenance(repeated), /duplicate/);
  const real = clone();
  real.cases[0].evidence = {origin: "REAL_GAME", verification: "USER_VERIFIED",
    references: [{id: "local-proof", description: "not a real image; structural test only"}]};
  for (const f of [real.cases[0].checks.pp.pokemon.species,
    real.cases[0].checks.pp.move_id, real.cases[0].checks.pp.observed_battle_max_pp]) {
    f.source = "GAME_OBSERVED";
    f.evidence_ids = ["unknown"];
  }
  assert.throws(() => assertEvidenceProvenance(real), /GAME_OBSERVED/);
  // Declared and linked provenance is structurally valid but not image authenticity.
  for (const f of [real.cases[0].checks.pp.pokemon.species,
    real.cases[0].checks.pp.move_id, real.cases[0].checks.pp.observed_battle_max_pp])
    f.evidence_ids = ["local-proof"];
  assert.equal(assertEvidenceProvenance(real).source_counts.GAME_OBSERVED, 3);
});
test("golden comparator disagreement or unreviewed status fails closed", () => {
  const compared = goldenRun(file);
  const suspicious = structuredClone(compared);
  suspicious.cases[0].checks[0].status = "PASS";
  assert.throws(() => summarizeGolden(doc, suspicious, input), /unexpected status/);
  suspicious.cases[0].checks[0].status = "MATCH_SYNTHETIC";
  suspicious.input_sha256 = "0".repeat(64);
  assert.throws(() => summarizeGolden(doc, suspicious, input), /source hash changed/);
  const base = summarizeGolden(doc, compared, input);
  assert.equal(base.comparison_status, "NO_VERIFIED_SCALAR_CONCLUSION");
});
test("partial input UNKNOWN remains UNKNOWN with no golden promotion", () => {
  const partial = {schema: "phase0-partial-observation-v1",
    origin: "SYNTHETIC", verification: "UNVERIFIED", evidence: [],
    facts: Object.fromEntries(PARTIAL_FIELDS.map(k => [k, {source: "UNKNOWN"}]))};
  const assessment = assertObservation(partial, context());
  const board = summarizePartial(partial, assessment, Buffer.from(JSON.stringify(partial)));
  assert.equal(board.unknown_field_count, PARTIAL_FIELDS.length);
  assert.equal(board.game_observed_field_count, 0);
  assert.equal(board.complete_snapshot_restorable_from_observation, false);
  assert.equal(createReport(null, board).B_full_snapshot_created, false);
  partial.facts.opponent_hp_exact = {source: "INFERRED", value: 99, reason: "guess"};
  const updated = summarizePartial(partial, assertObservation(partial, context()),
    Buffer.from(JSON.stringify(partial)));
  assert.equal(updated.inferred_field_count, 1);
  assert.ok(updated.inferred_fields.includes("opponent_hp_exact"));
  assert.equal(updated.game_observed_field_count, 0);
  partial.facts.opponent_hp_exact = {source: "GAME_OBSERVED", value: 99,
    evidence_ids: ["none"]};
  assert.throws(() => assertObservation(partial, context()),
    /fact evidence missing|game observation lacks verified game evidence/);
});
test("a Golden check is not a complete midbattle state even when user-verified", () => {
  const compared = goldenRun(file);
  const real = structuredClone(doc);
  real.cases[0].evidence = {origin: "REAL_GAME", verification: "USER_VERIFIED",
    references: [{id: "simulated-ref", description: "STRUCTURAL_STUB_NOT_REAL_GAME"}]};
  for (const f of [real.cases[0].checks.pp.pokemon.species,
    real.cases[0].checks.pp.move_id, real.cases[0].checks.pp.observed_battle_max_pp]) {
    f.source = "GAME_OBSERVED";
    f.evidence_ids = ["simulated-ref"];
  }
  const notRealComparison = structuredClone(compared);
  notRealComparison.cases[0].origin = "REAL_GAME";
  notRealComparison.cases[0].verification = "USER_VERIFIED";
  notRealComparison.cases[0].checks[0].status = "MATCH";
  notRealComparison.counts.verified_game_check_count = 1;
  notRealComparison.counts.verified_game_match_check_count = 1;
  notRealComparison.counts.verified_game_matching_case_count = 1;
  const stubBytes = Buffer.from(JSON.stringify(real));
  notRealComparison.input_sha256 =
    crypto.createHash("sha256").update(stubBytes).digest("hex");
  notRealComparison.counts.synthetic = 0;
  const result = summarizeGolden(real, notRealComparison, stubBytes);
  assert.equal(result.comparison_status, "USER_DECLARED_EVIDENCE_MATCHED_SCALAR");
  assert.equal(result.b_midbattle_fields_transferred, 0);
  assert.equal(createReport(result).B_full_snapshot_created, false);
});

test("an externally swapped document or comparator metadata is rejected", () => {
  const comparison = goldenRun(file);
  const changed = structuredClone(doc);
  changed.cases[0].case_id = "changed";
  assert.throws(() => summarizeGolden(changed, comparison, input),
    /document differs from independently retained input bytes/);
  const tampered = structuredClone(comparison);
  tampered.cases[0].origin = "REAL_GAME";
  assert.throws(() => summarizeGolden(doc, tampered, input),
    /comparator contract disagrees/);
});

test("Golden v1 factual provenance is strict without breaking optional legacy inference", () => {
  const make = () => structuredClone(doc);
  const unknownWithIds = make();
  unknownWithIds.cases[0].checks.pp.observed_remaining_pp.evidence_ids = ["bogus"];
  assert.throws(() => assertEvidenceProvenance(unknownWithIds),
    /UNKNOWN cannot carry evidence/);
  const unknownWithReason = make();
  unknownWithReason.cases[0].checks.pp.observed_remaining_pp.reason = "invented";
  assert.throws(() => assertEvidenceProvenance(unknownWithReason),
    /UNKNOWN cannot carry evidence/);
  const syntheticUnlinked = make();
  syntheticUnlinked.cases[0].checks.pp.move_id.evidence_ids = ["undeclared"];
  assert.throws(() => assertEvidenceProvenance(syntheticUnlinked),
    /synthetic evidence reference/);
  const syntheticLinked = make();
  syntheticLinked.cases[0].evidence.references.push({
    id:"fixture-id",description:"synthetic, not an actual game frame"});
  syntheticLinked.cases[0].checks.pp.move_id.evidence_ids = ["fixture-id"];
  assert.equal(assertEvidenceProvenance(syntheticLinked).source_counts.GAME_OBSERVED,0);
  const legacy = make();
  legacy.cases[0].checks.pp.move_id.source = "INFERRED";
  const accepted = assertEvidenceProvenance(legacy);
  assert.equal(accepted.legacy_inferred_without_reason_count,1);
  assert.equal(accepted.golden_inference_provenance_scope,
    "LEGACY_V1_UNEXPLAINED_INFERENCES_NEED_LOCAL_REVIEW");
  legacy.cases[0].checks.pp.move_id.reason = "test inference, not observed";
  assert.equal(assertEvidenceProvenance(legacy).legacy_inferred_without_reason_count,0);
  legacy.cases[0].checks.pp.move_id.reason = " ";
  assert.throws(() => assertEvidenceProvenance(legacy), /inference reason/);
  legacy.cases[0].checks.pp.move_id.reason = "test inference";
  legacy.cases[0].checks.pp.move_id.evidence_ids = ["fixture-id"];
  assert.throws(() => assertEvidenceProvenance(legacy),
    /inference must have a value/);
});
test("structural REAL_GAME partial declaration runs through real file path, never authenticates", () => {
  withTempFiles(dir => {
    const partialPath = path.join(dir,"STRUCTURAL_STUB_NOT_REAL_GAME_PRIVATE_PARTIAL.json");
    fs.writeFileSync(partialPath,JSON.stringify(structuralPartialStub()));
    const standalone = run({partialPath});
    assert.equal(standalone.golden_scalar,null);
    assert.equal(standalone.partial_board.game_observed_field_count,1);
    assert.equal(standalone.partial_board.inferred_field_count,1);
    assert.equal(standalone.partial_board.unknown_field_count,PARTIAL_FIELDS.length-2);
    assert.equal(standalone.partial_board.source_evidence_automatically_authenticated,false);
    assert.equal(standalone.partial_board.unique_truth_restoration,false);
    assert.equal(standalone.partial_board.complete_snapshot_restorable_from_observation,false);
    assert.equal(standalone.observations_independently_authenticated,false);
    assert.equal(standalone.B_full_snapshot_created,false);
    assert.equal(standalone.game_golden_promoted_to_B,false);
    assert.equal(standalone.evidence_files_automatically_joined_as_same_battle,false);
    assert.deepEqual([standalone.gate_A,standalone.gate_B,standalone.adoption],
      ["UNKNOWN","UNSUPPORTED","NO_GO"]);
    const both=run({goldenPath:file,partialPath});
    assert.equal(both.golden_scalar.origin_counts.REAL_GAME,0);
    assert.equal(both.golden_scalar.comparison_counts.verified_game_check_count,0);
    assert.equal(both.partial_board.game_observed_field_count,1);
    assert.equal(both.evidence_files_automatically_joined_as_same_battle,false);
    assert.equal(both.game_golden_promoted_to_B,false);
    assert.equal(both.B_full_snapshot_created,false);
    const serialized=JSON.stringify(both);
    for (const secret of [
      "STRUCTURAL_STUB_NOT_REAL_GAME_SENSITIVE_REFERENCE_987",
      "STRUCTURAL_STUB_NOT_REAL_GAME_PREDICTION_ONLY",
      "STRUCTURAL_STUB_NOT_REAL_GAME_PRIVATE_PARTIAL.json"
    ]) assert.equal(serialized.includes(secret),false,"private evidence leak");
    const cli=runCli(["--golden",file,"--partial",partialPath]);
    assert.equal(cli.code,0,cli.output);
    const printed=JSON.parse(cli.output);
    assert.equal(printed.B_full_snapshot_created,false);
    assert.equal(printed.evidence_files_automatically_joined_as_same_battle,false);
    assert.equal(printed.golden_scalar.origin_counts.REAL_GAME,0);
    for (const secret of [
      "STRUCTURAL_STUB_NOT_REAL_GAME_SENSITIVE_REFERENCE_987",
      "STRUCTURAL_STUB_NOT_REAL_GAME_PREDICTION_ONLY",
      "STRUCTURAL_STUB_NOT_REAL_GAME_PRIVATE_PARTIAL.json"
    ]) assert.equal(cli.output.includes(secret),false,"CLI private evidence leak");
    // No image is supplied, verified or counted: this is structural validation only.
  });
});
test("CLI sanitizes private-path/ID/value errors on invalid partial or Golden", () => {
  withTempFiles(dir => {
    const partialPath=path.join(dir,"SECRET_USER_FILENAME_349.json");
    const poisoned=structuralPartialStub();
    poisoned.facts.opponent_hp_exact.evidence_ids=["SECRET_UNLINKED_ID_871"];
    fs.writeFileSync(partialPath,JSON.stringify(poisoned));
    const invalidPartial=runCli(["--partial",partialPath]);
    assert.equal(invalidPartial.code,2);
    assert.deepEqual(JSON.parse(invalidPartial.output),{
      status:"NOT_RUN",reason:"PRIVATE_EVIDENCE_VALIDATION_FAILED"});
    assert.equal(invalidPartial.stderr,"");
    for(const secret of ["SECRET_USER_FILENAME_349","SECRET_UNLINKED_ID_871"])
      assert.equal(invalidPartial.output.includes(secret),false);
    const goldenPath=path.join(dir,"SECRET_GOLDEN_FILENAME_951.json");
    const invalidGolden=structuredClone(doc);
    invalidGolden.cases[0].checks.pp.observed_remaining_pp={
      source:"UNKNOWN",value:"SECRET_POKEMON_HP_439"};
    fs.writeFileSync(goldenPath,JSON.stringify(invalidGolden));
    const brokenGolden=runCli(["--golden",goldenPath]);
    assert.equal(brokenGolden.code,2);
    assert.deepEqual(JSON.parse(brokenGolden.output),{
      status:"NOT_RUN",reason:"PRIVATE_EVIDENCE_VALIDATION_FAILED"});
    assert.equal(brokenGolden.stderr,"");
    assert.equal(brokenGolden.output.includes("SECRET_POKEMON_HP_439"),false);
    assert.equal(brokenGolden.output.includes("SECRET_GOLDEN_FILENAME_951"),false);
  });
});
