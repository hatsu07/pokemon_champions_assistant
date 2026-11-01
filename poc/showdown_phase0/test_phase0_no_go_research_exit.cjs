"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {disposition,load}=require("./phase0_no_go_research_exit.cjs");
const {PIN,FORMAT,INPUTS}=require("./phase0_final_adoption_report.cjs");
function fixture(){
 return {
  schema:"phase0-final-adoption-evidence-v1",pinned_sha:PIN,
  format:FORMAT,regulation:"M-C",scope:"OFFLINE_SYNTHETIC_AND_SOURCE_REPORT_ONLY",
  source_artifacts:INPUTS.map(([key,file])=>({key,file,read_and_validated:true})),
  gates:{A:"UNKNOWN",B:"UNSUPPORTED",phase1_adoption:"NO_GO",
   full_engine_integration_permitted:false,live_recommendations_permitted:false},
  gate_A:{feature_count:10,features:Array.from({length:10},(_,i)=>({
   feature:"feature_"+i,feature_compatibility:"UNKNOWN",
   certain_ko_or_safe_switch_approved:false
  })),known_source_method_disagreements:1,discriminating_damage_rolls:4,
   synthetic_executed_damage_turns:16,game_verified_cases_in_ci:0,
   game_verified_checks_in_ci:0,device_golden_read:false},
  gate_B:{full_synthetic_snapshots_passed:12,partial_projection_cases:2,
   partial_projection_status:"INCONCLUSIVE",distinct_full_snapshot_inputs:11,
   independent_partial_hypothesis_cases:26,reconstructs_real_game_partial_state:false},
  performance:{synthetic_representative_state_cases:127,synthetic_conditions_met:true,
   cross_process_runs:3,cases_per_process:127,cross_process_output_equal:true,
   p95_product_e2e_measured:false,p95_3000ms_product_e2e_met:false,
   windows_app_included_node_worker_measured:false,peak_rss_measured:false,
   long_term_leak_tested:false},
  deployment:{pinned_upstream_sha:PIN,
   windows_onedir_offline_worker:"NOT_TESTED",windows_ipc_worker_recovery:"NOT_TESTED",
   android_ocr_to_gui_e2e:"NOT_TESTED",npm_dependency_license_audit:"NOT_TESTED",
   sbom_and_redistribution_notices:"NOT_TESTED",build_container_is_product:false},
  real_game_evidence:{ci_input_game_observations:0,private_golden_read:false,
   issue_139_reported_local_cases:6,issue_139_reported_local_checks:11,
   independent_battles_not_established:true,
   provenance:"ISSUE_139_USER_VERIFIED_LOCAL_HISTORICAL_SUMMARY_NOT_REEXECUTED_IN_CI"},
  alternatives:[
   {id:"full_showdown",recommendation:"DO_NOT_ADOPT"},
   {id:"limited_offline",recommendation:"RESEARCH_ONLY"},
   {id:"current_live",recommendation:"KEEP_UNCHANGED"}
  ],
  phase0_ready_to_close:false,evidence_does_not_enable_phase1:true
 };
}
test("NO_GO can finish a research PoC without ever permitting production adoption",()=>{
 const r=disposition(fixture());
 assert.equal(r.schema,"phase0-no-go-research-exit-v1");
 assert.equal(r.research_disposition,"NO_GO_EXIT_EVIDENCE_READY_FOR_MANUAL_REVIEW");
 assert.deepEqual(r.gates,{A:"UNKNOWN",B:"UNSUPPORTED",phase1:"NO_GO"});
 assert.equal(r.issue_139_no_go_exit_can_be_proposed,true);
 assert.equal(r.issue_139_close_automatically,false);
 assert.equal(r.require_latest_head_ci_and_code_review,true);
 for(const field of ["does_not_assert_game_parity",
  "does_not_assert_real_game_partial_state_restoration",
  "does_not_assert_product_e2e_or_redistribution_readiness"]){
   assert.equal(r[field],true);
 }
 assert.equal(r.live_recommendations_permitted,false);
 assert.equal(r.full_engine_integration_permitted,false);
 assert.equal(r.ci_game_observations,0);
 assert.equal(r.private_golden_read,false);
 assert.deepEqual(r.tracked_source_reports,INPUTS.map(([k])=>k));
 assert.deepEqual(r.blockers_to_GO.map(x=>x.id),["A","B","D","P","L"]);
 assert.equal(r.chosen_path,"KEEP_CURRENT_LIVE_AND_OPTIONAL_OFFLINE_RESEARCH");
 assert.deepEqual(r.carried_historical_local_golden,{
   cases:6,checks:11,not_six_independent_battles:true,not_rerun_in_ci:true});
});
test("forgery of parity, product evidence, or selected alternative is rejected",()=>{
 const changes=[
   o=>{o.pinned_sha="not-the-fixed-pin";},
   o=>{o.schema="phase0-final-adoption-evidence-v2";},
   o=>{o.scope="GAME_TESTED";},
   o=>{o.source_artifacts.pop();},
   o=>{o.source_artifacts[0].read_and_validated=false;},
   o=>{o.source_artifacts[0].key="fake";},
   o=>{o.gates.A="CONFIRMED";},
   o=>{o.gates.B="PARTIAL";},
   o=>{o.gates.phase1_adoption="GO";},
   o=>{o.gates.live_recommendations_permitted=true;},
   o=>{o.gates.full_engine_integration_permitted=true;},
   o=>{o.phase0_ready_to_close=true;},
   o=>{o.evidence_does_not_enable_phase1=false;},
   o=>{o.gate_A.features.pop();},
   o=>{o.gate_A.features[0].feature=o.gate_A.features[1].feature;},
   o=>{o.gate_A.features[0].feature_compatibility="CONFIRMED";},
   o=>{o.gate_A.features[0].certain_ko_or_safe_switch_approved=true;},
   o=>{o.gate_A.known_source_method_disagreements=0;},
   o=>{o.gate_A.discriminating_damage_rolls=0;},
   o=>{o.gate_A.device_golden_read=true;},
   o=>{o.gate_B.reconstructs_real_game_partial_state=true;},
   o=>{o.gate_B.partial_projection_cases=0;},
   o=>{o.gate_B.independent_partial_hypothesis_cases=0;},
   o=>{o.performance.p95_product_e2e_measured=true;},
   o=>{o.performance.p95_3000ms_product_e2e_met=true;},
   o=>{o.performance.windows_app_included_node_worker_measured=true;},
   o=>{o.deployment.windows_onedir_offline_worker="TESTED";},
   o=>{o.deployment.npm_dependency_license_audit="PASS";},
   o=>{o.deployment.sbom_and_redistribution_notices="PASS";},
   o=>{o.real_game_evidence.ci_input_game_observations=11;},
   o=>{o.real_game_evidence.private_golden_read=true;},
   o=>{o.real_game_evidence.independent_battles_not_established=false;},
   o=>{o.alternatives[0].recommendation="ADOPT";},
   o=>{o.alternatives[2].recommendation="DO_NOT_USE";}
 ];
 for(const [i,change] of changes.entries()){
   const f=fixture();change(f);
   assert.throws(()=>disposition(f),/PHASE0_EXIT_EVIDENCE_INVALID/,"mutation "+i);
 }
});
test("missing or unreadable source disposition fails closed",()=>{
 assert.throws(()=>load(""),/PHASE0_EXIT_EVIDENCE_INVALID/);
 assert.throws(()=>load("/nonexistent-phase0-research-report.json"),
   /PHASE0_EXIT_EVIDENCE_INVALID/);
});
