"use strict";
const REQUIRED = ["normal", "hp_status_pp_boost", "item_choice", "weather_screens",
  "consumed_item", "forced_switch", "mega_history"];
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
  const gameValidated = Array.isArray(realGameGoldens) && realGameGoldens.length > 0 &&
    realGameGoldens.every(x => x?.verified === true && x?.matched === true);
  // The PoC cannot certify Champions equivalence from simulator-only data.
  const gateA = gameValidated ? "PARTIAL" : "UNKNOWN";
  // Arbitrary real-world midbattle reconstruction is a separate unsolved gate.
  const gateB = restoration.length ? "UNSUPPORTED" : "PARTIAL";
  return {gate_A_calculation: gateA, gate_B_midbattle: gateB,
    missing_restore_cases: restoration, synthetic_hypotheses_ok: hypothesisOk,
    performance_100_valid: measured, game_golden_cases: realGameGoldens.length,
    recommended_adoption: "NO_GO", final_adoption_gate_implemented: false,
    gate_A_requires_real_game_goldens: true,
    gate_B_requires_partial_observation_reconstruction: true,
    reason: "Synthetic serialization and local performance cannot prove real Champions compatibility or arbitrary observed-state restoration."};
}
module.exports = {REQUIRED, assess};
