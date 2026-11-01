"use strict";
// Offline evidence boundary for Issue #139. Does not ingest image/video or
// promote an A (stat/PP/damage) observation into a B (midbattle) snapshot.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const HASH = b => crypto.createHash("sha256").update(b).digest("hex");
const ORIGINS = new Set(["REAL_GAME", "SYNTHETIC"]);
const FACT_SOURCES = new Set(["GAME_OBSERVED", "SYNTHETIC", "INFERRED", "UNKNOWN"]);
const GATES = Object.freeze({gate_A: "UNKNOWN", gate_B: "UNSUPPORTED", adoption: "NO_GO"});
const PARTIAL_SCHEMA = require("./partial_observation_schema.json");
const PARTIAL_FIELDS = [...PARTIAL_SCHEMA.properties.facts.required];

function factsIn(value, at = "$", out = []) {
  if (!value || typeof value !== "object") return out;
  if (Array.isArray(value)) {
    value.forEach((v, i) => factsIn(v, at + "[" + i + "]", out));
  } else if (Object.hasOwn(value, "source")) {
    if (!FACT_SOURCES.has(value.source)) throw Error("invalid fact source at " + at);
    out.push({at, fact: value});
  } else {
    for (const [key, v] of Object.entries(value)) factsIn(v, at + "." + key, out);
  }
  return out;
}
function assertEvidenceProvenance(doc) {
  if (doc?.schema !== "champions-real-game-golden-v1" ||
      !Array.isArray(doc.cases) || doc.cases.length === 0)
    throw Error("missing or invalid golden cases");
  let facts = 0, real = 0, inferred = 0, unknown = 0, synthetic = 0;
  let legacyInferredWithoutReason = 0;
  const ids = new Set();
  for (const c of doc.cases) {
    if (typeof c.case_id !== "string" || !c.case_id || ids.has(c.case_id))
      throw Error("missing or duplicate case identity");
    ids.add(c.case_id);
    const ev = c.evidence;
    if (!ev || !ORIGINS.has(ev.origin) ||
        !["USER_VERIFIED", "UNVERIFIED"].includes(ev.verification) ||
        (ev.origin === "SYNTHETIC" && ev.verification !== "UNVERIFIED") ||
        !Array.isArray(ev.references))
      throw Error("invalid evidence origin/verification");
    const refs = new Set();
    for (const ref of ev.references) {
      if (typeof ref?.id !== "string" || !ref.id || refs.has(ref.id))
        throw Error("duplicate or empty evidence reference");
      refs.add(ref.id);
    }
    for (const {fact} of factsIn(c.checks)) {
      facts++;
      if (fact.source === "UNKNOWN") {
        if (Object.hasOwn(fact, "value")) throw Error("UNKNOWN carries a value");
        if (Object.hasOwn(fact, "evidence_ids") || Object.hasOwn(fact, "reason"))
          throw Error("UNKNOWN cannot carry evidence or inference metadata");
        unknown++;
      } else if (fact.source === "INFERRED") {
        if (!Object.hasOwn(fact, "value") || Object.hasOwn(fact, "evidence_ids"))
          throw Error("inference must have a value and no observed evidence IDs");
        // Golden v1 legacy facts can omit reason. Keep them loadable but
        // explicitly report the missing reason; never turn them into observed facts.
        if (fact.reason === undefined) legacyInferredWithoutReason++;
        else if (typeof fact.reason !== "string" || !fact.reason.trim())
          throw Error("inference reason must be nonempty");
        inferred++;
      } else if (fact.source === "SYNTHETIC") {
        if (ev.origin !== "SYNTHETIC" || !Object.hasOwn(fact, "value") ||
            Object.hasOwn(fact, "reason"))
          throw Error("synthetic fact falsely sourced");
        if (Object.hasOwn(fact, "evidence_ids") &&
            (!Array.isArray(fact.evidence_ids) || !fact.evidence_ids.length ||
             fact.evidence_ids.some(id => typeof id !== "string" || !refs.has(id))))
          throw Error("synthetic evidence reference is missing or undeclared");
        // Historical Golden v1 synthetic fixtures often have no evidence ID;
        // that is legal synthetic evidence, never proof from a real game.
        synthetic++;
      } else {
        if (ev.origin !== "REAL_GAME" || ev.verification !== "USER_VERIFIED" ||
            !Object.hasOwn(fact, "value") || Object.hasOwn(fact, "reason") ||
            !Array.isArray(fact.evidence_ids) || !fact.evidence_ids.length ||
            fact.evidence_ids.some(id => typeof id !== "string" || !refs.has(id)))
          throw Error("GAME_OBSERVED lacks declared, linked user-verified evidence");
        real++;
      }
    }
  }
  return {facts, source_counts: {GAME_OBSERVED: real, SYNTHETIC: synthetic,
    INFERRED: inferred, UNKNOWN: unknown},
    legacy_inferred_without_reason_count: legacyInferredWithoutReason,
    golden_inference_provenance_scope: legacyInferredWithoutReason ?
      "LEGACY_V1_UNEXPLAINED_INFERENCES_NEED_LOCAL_REVIEW" :
      "NO_UNEXPLAINED_INFERENCES"};
}
function summarizeGolden(doc, comparison, bytes) {
  const validated = assertEvidenceProvenance(doc);
  if (JSON.stringify(JSON.parse(bytes.toString("utf8"))) !== JSON.stringify(doc))
    throw Error("source document differs from independently retained input bytes");
  if (!comparison || comparison.schema !== "champions-golden-comparison-v2" ||
      comparison.gate_A_calculation !== "UNKNOWN" ||
      comparison.cases?.length !== doc.cases.length ||
      !comparison.counts || !comparison.input_sha256 ||
      comparison.cases.some((c, i) => c.case_id !== doc.cases[i].case_id ||
        c.origin !== doc.cases[i].evidence.origin ||
        c.verification !== doc.cases[i].evidence.verification))
    throw Error("golden comparator contract disagrees with evidence");
  const statuses = comparison.cases.flatMap(c => c.checks.map(x => x.status));
  if (statuses.some(s => !["MATCH", "MISMATCH", "INCONCLUSIVE",
      "MATCH_SYNTHETIC", "MISMATCH_SYNTHETIC",
      "MATCH_UNVERIFIED", "MISMATCH_UNVERIFIED"].includes(s)))
    throw Error("golden result contains unexpected status");
  const verified = comparison.counts.verified_game_check_count;
  const matches = comparison.counts.verified_game_match_check_count;
  const discordant = comparison.cases.flatMap(c =>
    c.checks.filter(x => x.status === "MISMATCH"));
  const matchStatuses = statuses.filter(s => s.startsWith("MATCH")).length;
  const mismatchStatuses = statuses.filter(s => s.startsWith("MISMATCH")).length;
  const verifiedRows = comparison.cases
    .filter(c => c.origin === "REAL_GAME" && c.verification === "USER_VERIFIED")
    .flatMap(c => c.checks.filter(x => x.status === "MATCH" || x.status === "MISMATCH"));
  if (matches > verified || verified > statuses.length ||
      verified !== verifiedRows.length ||
      matches !== verifiedRows.filter(x => x.status === "MATCH").length ||
      comparison.counts.match !== matchStatuses ||
      comparison.counts.mismatch !== mismatchStatuses)
    throw Error("verified comparison accounting is inconsistent");
  const rawDigest = HASH(bytes);
  if (comparison.input_sha256 !== rawDigest)
    throw Error("golden source hash changed during evaluation");
  return {scope: "SCALAR_A_ONLY_NOT_B_BATTLE_STATE",
    origin_counts: {REAL_GAME: doc.cases.filter(c => c.evidence.origin === "REAL_GAME").length,
      SYNTHETIC: doc.cases.filter(c => c.evidence.origin === "SYNTHETIC").length},
    case_count: doc.cases.length, fact_source_counts: validated.source_counts,
    legacy_inferred_without_reason_count: validated.legacy_inferred_without_reason_count,
    golden_inference_provenance_scope: validated.golden_inference_provenance_scope,
    comparison_counts: {...comparison.counts},
    verified_game_mismatch_count: discordant.length,
    comparison_status: discordant.length ? "REVIEW_REQUIRED" :
      verified > 0 && matches === verified ? "USER_DECLARED_EVIDENCE_MATCHED_SCALAR" :
      "NO_VERIFIED_SCALAR_CONCLUSION",
    // SHA is of private input, but never input values, IDs, paths or reference metadata.
    input_sha256: rawDigest,
    basis: "USER_VERIFIED_IS_DECLARED_NOT_INDEPENDENTLY_IMAGE_AUTHENTICATED",
    b_midbattle_fields_transferred: 0,
    b_reconstruction_from_golden: false};
}
function summarizePartial(doc, assessment, bytes) {
  if (!doc || JSON.stringify(JSON.parse(bytes.toString("utf8"))) !== JSON.stringify(doc))
    throw Error("partial document differs from input bytes");
  if (doc.schema !== "phase0-partial-observation-v1" ||
      !ORIGINS.has(doc.origin) ||
      !["USER_VERIFIED", "UNVERIFIED"].includes(doc.verification) ||
      !assessment?.consistent ||
      !Array.isArray(assessment.unknown_fields) ||
      !Array.isArray(assessment.inferred_fields) ||
      !Array.isArray(assessment.observed_fields))
    throw Error("partial observation validator contract mismatch");
  const fields = Object.keys(doc.facts || {});
  if (fields.length !== PARTIAL_FIELDS.length ||
      PARTIAL_FIELDS.some(f => !Object.hasOwn(doc.facts, f)))
    throw Error("partial schema has missing fields");
  const gameFields = PARTIAL_FIELDS.filter(f => doc.facts[f].source === "GAME_OBSERVED");
  const syntheticFields = PARTIAL_FIELDS.filter(f => doc.facts[f].source === "SYNTHETIC");
  if ((doc.origin !== "REAL_GAME" || doc.verification !== "USER_VERIFIED") &&
      gameFields.length) throw Error("unverified game observation fields");
  return {scope: "PARTIAL_BOARD_FACTS_ONLY_NO_FULL_BATTLE_RESTORE",
    origin: doc.origin, verification_claim: doc.verification,
    field_count: PARTIAL_FIELDS.length,
    game_observed_field_count: gameFields.length, synthetic_field_count: syntheticFields.length,
    inferred_field_count: assessment.inferred_fields.length,
    unknown_field_count: assessment.unknown_fields.length,
    // Field names reveal no values, case identifiers, screenshots or provenance paths.
    game_observed_fields: gameFields, inferred_fields: assessment.inferred_fields,
    unknown_fields: assessment.unknown_fields,
    missing_internal_state: assessment.missing_internal_state,
    source_evidence_automatically_authenticated: false,
    input_sha256: HASH(bytes), unique_truth_restoration: false,
    complete_snapshot_restorable_from_observation: false,
    showdowns_internal_rng_observed: doc.facts.internal_rng.source === "GAME_OBSERVED",
    candidate_hypotheses_constructed: 0};
}
function createReport(golden = null, partial = null) {
  if (!golden && !partial) throw Error("an evidence input is required");
  return {schema: "phase0-local-evidence-boundary-v1",
    scope: "OFFLINE_ONLY; PRIVATE_INPUTS; NO_CROSS_SCHEMA_PROMOTION",
    pinned_showdown_sha: "ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
    format: "gen9championsbssregmc", regulation: "M-C",
    ...GATES,
    golden_scalar: golden, partial_board: partial,
    observations_independently_authenticated: false,
    evidence_files_automatically_joined_as_same_battle: false,
    game_golden_promoted_to_B: false,
    B_full_snapshot_created: false,
    reason: "verified scalar observations and partial board facts lack hidden state/RNG/history/queue"};
}
function run({goldenPath = null, partialPath = null} = {}) {
  if (!goldenPath && !partialPath) throw Error("at least one input is required");
  let golden = null, partial = null;
  if (goldenPath) {
    const bytes = fs.readFileSync(goldenPath);
    const doc = JSON.parse(bytes.toString("utf8"));
    // Provenance first: no source conflict can be counted as a verified MATCH.
    assertEvidenceProvenance(doc);
    const comparison = require("./golden_compare.cjs").run(goldenPath);
    golden = summarizeGolden(doc, comparison, bytes);
  }
  if (partialPath) {
    const bytes = fs.readFileSync(partialPath);
    const doc = JSON.parse(bytes.toString("utf8"));
    const {assertObservation} = require("./partial_observation_probe.cjs");
    const {context} = require("./six_team_selection_probe.cjs");
    const assessed = assertObservation(doc, context());
    partial = summarizePartial(doc, assessed, bytes);
  }
  return createReport(golden, partial);
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (!args.length || args.length % 2 !== 0) throw Error("invalid arguments");
    const files = {};
    for (let i = 0; i < args.length; i += 2) {
      if (!["--golden", "--partial"].includes(args[i]) ||
          files[args[i]] || !args[i + 1])
        throw Error("invalid or duplicate argument");
      files[args[i]] = path.resolve(args[i + 1]);
    }
    const report = run({goldenPath: files["--golden"], partialPath: files["--partial"]});
    process.stdout.write(JSON.stringify(report) + "\n");
    if (report.golden_scalar?.verified_game_mismatch_count) process.exitCode = 1;
  } catch (_error) {
    // Never echo file paths, IDs, Pokémon/move names or private values on error.
    process.stdout.write(JSON.stringify({status: "NOT_RUN",
      reason: "PRIVATE_EVIDENCE_VALIDATION_FAILED"}) + "\n");
    process.exitCode = 2;
  }
}
module.exports = {factsIn, assertEvidenceProvenance, summarizeGolden,
  summarizePartial, createReport, run, PARTIAL_FIELDS};
