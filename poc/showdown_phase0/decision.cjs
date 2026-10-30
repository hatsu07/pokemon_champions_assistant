"use strict";
const REQUIRED = ["normal", "hp_status_pp_boost", "item_choice", "weather_screens",
  "consumed_item", "forced_switch", "mega_history", "pending_choice"];
function assess(probe, bench, realGameGoldens = []) {
  const cases = probe?.results || {};
  const restoration = REQUIRED.filter(k => cases[k]?.status !== "PASS_SYNTHETIC");
  const hypothesisOk = cases.unknown_sets?.status === "PASS_SYNTHETIC" &&
    cases.unknown_sets?.changed_initial_model === true &&
    cases.unknown_sets?.same_observed_input === true &&
    cases.unknown_sets?.observed_outcomes_differ === true &&
    cases.unknown_sets?.sources_unchanged === true;
  const measured = bench?.counts?.branch_resolved >= 100 &&
    bench?.counts?.choice_legal >= 100 &&
    bench?.counts?.reconstructed_ok >= 100 &&
    Number.isFinite(bench?.p99_ms);
  // 100 fast repetitions are NOT 100 representative fixtures.
  const fullReplayConsistency = bench?.iterations > 0 &&
    bench?.counts?.branch_resolved === bench?.iterations &&
    bench?.counts?.unsupported === 0;
  const regressionPass = cases.replay_regression?.status === "PASS_SYNTHETIC" &&
    cases.replay_regression?.failure_count === 0 && cases.replay_regression?.attempts >= 32;
  const criticalProbesPass = ["normal", "hp_status_pp_boost", "item_choice",
    "weather_screens", "consumed_item", "forced_switch", "pending_choice",
    "unknown_sets"].every(k => cases[k]?.status === "PASS_SYNTHETIC");
  const currentRunReplayConsistency = fullReplayConsistency &&
    regressionPass && criticalProbesPass;
  const representativeCoverage = bench?.representative_100_cases_met === true &&
    bench?.unique_fixture_count >= 100 &&
    bench?.validated_manifest_case_count >= 100 &&
    Array.isArray(bench?.missing_required_groups) &&
    bench.missing_required_groups.length === 0;
  const gameValidated = Array.isArray(realGameGoldens) && realGameGoldens.length > 0 &&
    realGameGoldens.every(x => x?.verified === true && x?.matched === true);
  // The PoC cannot certify Champions equivalence from simulator-only data.
  const gateA = gameValidated ? "PARTIAL" : "UNKNOWN";
  // Arbitrary real-world midbattle reconstruction is a separate unsolved gate.
  const gateB = restoration.length ? "UNSUPPORTED" : "PARTIAL";
  return {gate_A_calculation: gateA, gate_B_midbattle: gateB,
    missing_restore_cases: restoration, synthetic_hypotheses_ok: hypothesisOk,
    performance_100_valid: measured, // legacy: iterations only, NOT representative case count
    performance_100_iterations_valid: measured,
    representative_100_cases_met: representativeCoverage,
    phase0_100_case_performance_requirement_met: measured && representativeCoverage,
    synthetic_replay_all_consistent: fullReplayConsistency,
    synthetic_replay_failure_count: bench?.counts?.unsupported ?? null,
    synthetic_probe_failure_count: criticalProbesPass ? 0 :
      ["normal", "hp_status_pp_boost", "item_choice", "weather_screens",
        "consumed_item", "forced_switch", "pending_choice", "unknown_sets"]
        .filter(k => cases[k]?.status !== "PASS_SYNTHETIC").length,
    replay_regression_failures: cases.replay_regression?.failure_count ?? null,
    replay_regression_pass: regressionPass,
    current_run_replay_consistency_pass: currentRunReplayConsistency,
    // Repeated independent CI runs and real observed-state goldens are absent.
    phase0_replay_acceptance_met: false,
    phase0_replay_acceptance_reason: "No independent repeated-run evidence or real-game reconstruction; all critical synthetic fixtures must remain mismatch-free.",
    benchmark_iterations: bench?.iterations || 0,
    benchmark_unique_fixtures: bench?.unique_fixture_count || 0,
    benchmark_manifest_case_count: bench?.manifest_case_count || 0,
    benchmark_validated_manifest_cases: bench?.validated_manifest_case_count || 0,
    benchmark_distinct_mechanism_tags: bench?.distinct_mechanism_tag_count || 0,
    game_golden_cases: realGameGoldens.length,
    recommended_adoption: "NO_GO", final_adoption_gate_implemented: false,
    gate_A_requires_real_game_goldens: true,
    gate_B_requires_partial_observation_reconstruction: true,
    reason: "Synthetic serialization and local performance cannot prove real Champions compatibility or arbitrary observed-state restoration."};
}
module.exports = {REQUIRED, assess};
