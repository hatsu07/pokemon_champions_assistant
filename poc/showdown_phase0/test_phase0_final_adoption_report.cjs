"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {evaluate,load,INPUTS}=require("./phase0_final_adoption_report.cjs");
const featureMap=require("./gate_a_evidence_map.json");
const gateBManifest=require("./gate_b_cases.json");
const partialManifest=require("./partial_observation_cases.json");
const representativeManifest=require("./representative_states_cases.json");
const STAGES=["prepare_ms","serialize_ms","restore_ms","branch_ms","alternative_ms","replay_ms"];
const TIMINGS=Object.fromEntries(STAGES.map((k,i)=>[k,0.1+i/10]));
const TIMING_STATS=Object.fromEntries(STAGES.map(k=>[k,{
 samples:127,p50:TIMINGS[k],p95:TIMINGS[k],p99:TIMINGS[k],max:TIMINGS[k]
}]));

const pinned="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const format="gen9championsbssregmc";
const base=(schema,shaKey)=>({schema,[shaKey]:pinned,format,gate_A:"UNKNOWN",
 gate_B:"UNSUPPORTED",adoption:"NO_GO"});
function fixture(){
 const features=featureMap.feature_map.map((entry,i)=>({
   feature:entry.feature,regulation:"M-C",feature_compatibility:"UNKNOWN",
   actual_game_status:"NOT_EVALUATED_PRIVATE_GOLDEN",
   allowed_as_certain_ko_or_safe_switch_proof:false,static_disagreement_count:i===0?1:0
 }));
 const gateBResults=gateBManifest.cases.map((entry,i)=>({
   case_id:entry.id,mechanism:entry.mechanism,phase:entry.phase,
   status:entry.expected_status,pinned_showdown_sha:pinned,
   evidence_class:entry.phase==="B1"?"SYNTHETIC":"SYNTHETIC_PARTIAL_OBSERVATION",
   input_state_sha256:"fake_unique_"+(i%11)
 }));
 const partialResults=partialManifest.cases.map(entry=>({
   case_id:entry.id,mechanism:entry.mechanism,status:entry.expected_status,
   evidence_class:"SYNTHETIC_ONLY",pinned_showdown_sha:pinned,format,
   unique_truth_restoration:false,complete_snapshot_restorable_from_observation:false
 }));
 const representativeResults=representativeManifest.cases.map(entry=>({
   case_id:entry.id,mechanism:entry.mechanism,status:entry.expected_status,
   evidence_class:"SYNTHETIC_ONLY",timings_ms:{...TIMINGS},
   branch:{source_unchanged:true,repeated_action_identical:true,distinct_outcome:true}
 }));
 return {
  gate_a:{...base("phase0-gate-a-evidence-inventory-v1","pinned_sha"),
   scope:"OFFLINE_SOURCE_DIAGNOSTIC_ONLY",regulation:"M-C",feature_count:10,
   features,known_modifier_source_conflicts:1,known_modifier_roll_discrepancies:4,
   damage_rounding_executed_synthetic_turns:16,
   damage_rounding_source_discriminating_rolls:4,
   damage_rounding_game_verified_turns:0,
   safe_to_integrate_with_live:false,private_golden_read:false,
   game_verified_cases_this_run:0,game_verified_checks_this_run:0},
  gate_b:{...base("phase0-gate-b-diagnostics-v1","pinned_showdown_sha"),
   case_count:14,mechanism_count:14,unique_input_state_count:11,
   counts:{B1:{PASS_SYNTHETIC:12,STATE_OR_QUEUE_MISMATCH:0,
    LOG_ONLY_MISMATCH:0,INCONCLUSIVE:0,UNSUPPORTED:0},
    B2:{PASS_SYNTHETIC:0,STATE_OR_QUEUE_MISMATCH:0,LOG_ONLY_MISMATCH:0,
     INCONCLUSIVE:2,UNSUPPORTED:0}},results:gateBResults,
   representative_100_cases_met:false},
  partial:{...base("phase0-partial-observation-diagnostics-v1","pinned_showdown_sha"),
   case_count:26,mechanism_count:26,representative_100_cases_met:false,
   results:partialResults,
   counts:Object.fromEntries(["CONSISTENT_HYPOTHESES","CONTRADICTORY_OBSERVATION",
    "INCONCLUSIVE","UNSUPPORTED","SYNTHETIC_TEST_PASS"].map(k=>
    [k,partialResults.filter(x=>x.status===k).length])),
   synthetic_condition_pass_count:partialResults.filter(x=>
    ["CONSISTENT_HYPOTHESES","SYNTHETIC_TEST_PASS"].includes(x.status)).length},
  representative:{...base("phase0-representative-synthetic-states-v1","pinned_showdown_sha"),
   case_count:127,counts:{PASS_SYNTHETIC:127},representative_100_synthetic_states_met:true,
   tactical_hash_scope:"ACTIVE_PAIR_COARSE_PROJECTION_ONLY",
   timing_scope:"SINGLE_LINUX_CONTAINER_WARM_NODE_INTERNAL_STAGES_ONLY",
   native_game_verification:false,windows_e2e_verified:false,
   timing_ms:structuredClone(TIMING_STATS),results:representativeResults,
   distinct_mechanisms:8,counts:{PASS_SYNTHETIC:127,UNSUPPORTED:0},
   memory_bytes:{peak_rss_measured:false,long_running_leak_tested:false}},
  cross_process:{...base("phase0-cross-process-synthetic-validation-v1","pinned_showdown_sha"),
   independent_os_process_count:3,cases_per_process:127,total_case_invocations:381,
   cross_process_signatures_equal:true,unique_real_game_cases:0,
   game_compatibility_verified:false,windows_gui_ipc_e2e_verified:false,
   independent_ci_runs_verified:false,
   process_memory:{peak_rss_measured:false,long_term_leak_tested:false},
   process_wall_ms:{scope:"CHILD_STARTUP_AND_127_CASES_NOT_CONTAINER_STARTUP",
    samples:3,p50:210,p95:250,p99:250,observations:[250,210,249]},
   node_stage_ms:{scope:"THREE_SEPARATE_PROCESSES_SAME_FIXED_CASES",
    values:Object.fromEntries(STAGES.map(k=>[k,
     {samples:381,p50:TIMINGS[k],p95:TIMINGS[k],p99:TIMINGS[k]}]))}}
 };
}
test("five source-only diagnostics produce NO_GO with explicit boundaries",()=>{
 assert.equal(INPUTS.length,5);
 const result=evaluate(fixture());
 assert.equal(result.schema,"phase0-final-adoption-evidence-v1");
 assert.deepEqual(result.gates,{A:"UNKNOWN",B:"UNSUPPORTED",phase1_adoption:"NO_GO",
  full_engine_integration_permitted:false,live_recommendations_permitted:false,
  reason:"Neither broad Champions-game calculation parity nor partial real-game midbattle reconstruction is proven"});
 assert.equal(result.gate_A.feature_count,10);
 assert.equal(result.gate_A.known_source_method_disagreements,1);
 assert.equal(result.gate_A.discriminating_damage_rolls,4);
 assert.equal(result.gate_B.full_synthetic_snapshots_passed,12);
 assert.equal(result.gate_B.partial_projection_cases,2);
 assert.equal(result.performance.synthetic_representative_state_cases,127);
 assert.equal(result.performance.cross_process_runs,3);
 assert.equal(result.performance.p95_product_e2e_measured,false);
 assert.equal(result.deployment.windows_onedir_offline_worker,"NOT_TESTED");
 assert.equal(result.deployment.sbom_and_redistribution_notices,"NOT_TESTED");
 assert.equal(result.real_game_evidence.ci_input_game_observations,0);
 assert.equal(result.real_game_evidence.issue_139_reported_local_checks,11);
 assert.equal(result.real_game_evidence.independent_battles_not_established,true);
 assert.equal(result.phase0_ready_to_close,false);
 assert.equal(result.evidence_does_not_enable_phase1,true);
 assert.deepEqual(result.alternatives.map(x=>x.recommendation),
   ["DO_NOT_ADOPT","RESEARCH_ONLY","KEEP_UNCHANGED"]);
});
test("missing evidence, wrong SHA, fabricated device truth, E2E or replay parity fail closed",()=>{
 const changes=[
  r=>{delete r.cross_process},
  r=>{r.gate_a.features.pop()},
  r=>{r.gate_a.features[0].feature="unreviewed_feature"},
  r=>{r.gate_a.features[0].regulation="UNKNOWN"},
  r=>{r.gate_b.results[0].status="STATE_OR_QUEUE_MISMATCH"},
  r=>{r.gate_b.results[0].mechanism="unreviewed_mechanism"},
  r=>{r.gate_b.results[13].case_id="B2-13"},
  r=>{r.gate_b.results[0].phase="B2"},
  r=>{r.partial.results[0].status="UNSUPPORTED"},
  r=>{r.partial.results[1].case_id="PO-01"},
  r=>{r.representative.results[0].status="UNSUPPORTED"},
  r=>{r.representative.results[0].mechanism="unreviewed_mechanism"},
  r=>{r.representative.results[0].timings_ms.prepare_ms=1000},
  r=>{r.representative.timing_ms.prepare_ms.p95=-1},
  r=>{r.representative.timing_ms.prepare_ms.p50=10},
  r=>{r.cross_process.process_wall_ms.observations[0]=1},
  r=>{r.cross_process.process_wall_ms.p95=0},
  r=>{r.cross_process.node_stage_ms.values.prepare_ms.samples=380},
  r=>{r.cross_process.node_stage_ms.values.prepare_ms.p95=-1},
  r=>{r.gate_a.features[0].feature_compatibility="CONFIRMED"},
  r=>{r.gate_a.features[0].actual_game_status="GAME_OBSERVED_MATCH"},
  r=>{r.gate_a.features[0].allowed_as_certain_ko_or_safe_switch_proof=true},
  r=>{r.gate_a.private_golden_read=true},
  r=>{r.gate_a.known_modifier_source_conflicts=0},
  r=>{r.gate_a.damage_rounding_game_verified_turns=16},
  r=>{r.gate_b.counts.B1.PASS_SYNTHETIC=14},
  r=>{r.gate_b.counts.B2.INCONCLUSIVE=0},
  r=>{r.gate_b.gate_B="CONFIRMED"},
  r=>{r.partial.results[0].unique_truth_restoration=true},
  r=>{r.partial.results[0].complete_snapshot_restorable_from_observation=true},
  r=>{r.partial.results.pop()},
  r=>{r.representative.case_count=381},
  r=>{r.representative.counts.PASS_SYNTHETIC=126},
  r=>{r.representative.windows_e2e_verified=true},
  r=>{r.representative.memory_bytes.peak_rss_measured=true},
  r=>{r.representative.timing_ms.prepare_ms.samples=0},
  r=>{r.cross_process.independent_ci_runs_verified=true},
  r=>{r.cross_process.total_case_invocations=1000},
  r=>{r.cross_process.cross_process_signatures_equal=false},
  r=>{r.cross_process.process_wall_ms.p99="fast"},
  r=>{r.cross_process.pinned_showdown_sha="not-pinned"}
 ];
 for(const change of changes){
  const f=structuredClone(fixture());change(f);
  assert.throws(()=>evaluate(f),/PHASE0_EVIDENCE_INVALID/);
 }
});
test("local file loading fails closed without genuine five diagnostics",()=>{
 assert.throws(()=>load({}),/PHASE0_EVIDENCE_INVALID/);
});
