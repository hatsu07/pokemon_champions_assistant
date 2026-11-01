"use strict";
// Phase 0 research EXIT is not a Phase 1 adoption approval.
// The input is the already fail-closed, five-diagnostic adoption report.
const fs=require("node:fs");
const {PIN,FORMAT,INPUTS}=require("./phase0_final_adoption_report.cjs");
const EXPECTED_KEYS=INPUTS.map(([key])=>key);
function check(cond,label){
 if(!cond)throw Error("PHASE0_EXIT_EVIDENCE_INVALID: "+label);
}
function disposition(report){
 check(report&&typeof report==="object"&&!Array.isArray(report),
   "missing adoption report");
 check(report.schema==="phase0-final-adoption-evidence-v1"&&
   report.pinned_sha===PIN&&report.format===FORMAT&&report.regulation==="M-C"&&
   report.scope==="OFFLINE_SYNTHETIC_AND_SOURCE_REPORT_ONLY",
   "pinned source report identity");
 check(Array.isArray(report.source_artifacts)&&
   report.source_artifacts.length===EXPECTED_KEYS.length&&
   report.source_artifacts.every((x,i)=>x?.key===EXPECTED_KEYS[i]&&
     x.file===INPUTS[i][1]&&x.read_and_validated===true),
   "required five source diagnostic reports");
 const g=report.gates,a=report.gate_A,b=report.gate_B,
   perf=report.performance,dep=report.deployment,game=report.real_game_evidence;
 check(g?.A==="UNKNOWN"&&g.B==="UNSUPPORTED"&&
   g.phase1_adoption==="NO_GO"&&
   g.full_engine_integration_permitted===false&&
   g.live_recommendations_permitted===false&&
   report.phase0_ready_to_close===false&&
   report.evidence_does_not_enable_phase1===true,
   "NO_GO acceptance and LIVE blockade");
 check(a?.feature_count===10&&Array.isArray(a.features)&&
   a.features.length===10&&new Set(a.features.map(x=>x.feature)).size===10&&
   a.features.every(x=>x.feature_compatibility==="UNKNOWN"&&
     x.certain_ko_or_safe_switch_approved===false)&&
   a.known_source_method_disagreements===1&&
   a.discriminating_damage_rolls===4&&
   a.synthetic_executed_damage_turns===16&&
   a.game_verified_cases_in_ci===0&&a.game_verified_checks_in_ci===0&&
   a.device_golden_read===false,
   "Gate A uncertainty/known rounding divergence");
 check(b?.full_synthetic_snapshots_passed===12&&
   b.partial_projection_cases===2&&b.partial_projection_status==="INCONCLUSIVE"&&
   b.distinct_full_snapshot_inputs===11&&
   b.independent_partial_hypothesis_cases===26&&
   b.reconstructs_real_game_partial_state===false,
   "Gate B synthetic-only and partial unknown");
 check(perf?.synthetic_representative_state_cases===127&&
   perf.synthetic_conditions_met===true&&
   perf.cross_process_runs===3&&perf.cases_per_process===127&&
   perf.cross_process_output_equal===true&&
   perf.p95_product_e2e_measured===false&&
   perf.p95_3000ms_product_e2e_met===false&&
   perf.windows_app_included_node_worker_measured===false&&
   perf.peak_rss_measured===false&&perf.long_term_leak_tested===false,
   "synthetic performance must not become product evidence");
 check(dep?.pinned_upstream_sha===PIN&&
   dep.windows_onedir_offline_worker==="NOT_TESTED"&&
   dep.windows_ipc_worker_recovery==="NOT_TESTED"&&
   dep.android_ocr_to_gui_e2e==="NOT_TESTED"&&
   dep.npm_dependency_license_audit==="NOT_TESTED"&&
   dep.sbom_and_redistribution_notices==="NOT_TESTED"&&
   dep.build_container_is_product===false,
   "Windows deployment and license limitations");
 check(game?.ci_input_game_observations===0&&
   game.private_golden_read===false&&
   game.issue_139_reported_local_cases===6&&
   game.issue_139_reported_local_checks===11&&
   game.independent_battles_not_established===true&&
   game.provenance==="ISSUE_139_USER_VERIFIED_LOCAL_HISTORICAL_SUMMARY_NOT_REEXECUTED_IN_CI",
   "historic local Golden is not CI device parity");
 check(JSON.stringify(report.alternatives?.map(x=>[x.id,x.recommendation]))===
   JSON.stringify([
    ["full_showdown","DO_NOT_ADOPT"],
    ["limited_offline","RESEARCH_ONLY"],
    ["current_live","KEEP_UNCHANGED"]]),
   "fall back to existing LIVE and keep offline probe isolated");
 return {
  schema:"phase0-no-go-research-exit-v1",
  pinned_sha:PIN,format:FORMAT,regulation:"M-C",
  research_disposition:"NO_GO_EXIT_EVIDENCE_READY_FOR_MANUAL_REVIEW",
  decision_kind:"RESEARCH_POC_DISPOSITION_ONLY",
  gates:{A:"UNKNOWN",B:"UNSUPPORTED",phase1:"NO_GO"},
  does_not_assert_game_parity:true,
  does_not_assert_real_game_partial_state_restoration:true,
  does_not_assert_product_e2e_or_redistribution_readiness:true,
  full_engine_integration_permitted:false,
  live_recommendations_permitted:false,
  private_golden_read:false,ci_game_observations:0,
  tracked_source_reports:EXPECTED_KEYS,
  carried_historical_local_golden:{cases:6,checks:11,
   not_six_independent_battles:true,not_rerun_in_ci:true},
  blockers_to_GO:[
   {id:"A",status:"UNKNOWN",need:"High-impact Champions mechanics corroborated by actual game evidence"},
   {id:"B",status:"UNSUPPORTED",need:"Observed partial midbattle state modeled safely with independent hypotheses"},
   {id:"D",status:"NOT_TESTED",need:"Packaged Windows offline Node worker/IPC failure and recovery"},
   {id:"P",status:"NOT_TESTED",need:"Actual capture-to-GUI latency, memory and endurance"},
   {id:"L",status:"NOT_TESTED",need:"Dependency license audit, SBOM and redistribution notices"}
  ],
  chosen_path:"KEEP_CURRENT_LIVE_AND_OPTIONAL_OFFLINE_RESEARCH",
  issue_139_no_go_exit_can_be_proposed:true,
  issue_139_close_automatically:false,
  require_latest_head_ci_and_code_review:true,
  requirement_to_reopen_for_adoption:"New real-game evidence plus independently reevaluated Gate A/B and product requirements",
  warning:"Closing Phase 0 research as NO_GO is not passing Phase 0 for Phase 1. Do not merge local engine into production."
 };
}
function load(file){
 check(typeof file==="string"&&file.length>0,"one source report file required");
 let r;try{r=JSON.parse(fs.readFileSync(file,"utf8"));}
 catch(e){throw Error("PHASE0_EXIT_EVIDENCE_INVALID: unreadable report: "+e.message);}
 return disposition(r);
}
if(require.main===module){
 try{
  check(process.argv.length===3,"expected exactly one report JSON path");
  process.stdout.write(JSON.stringify(load(process.argv[2]))+"\n");
 }catch(e){
  process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");
  process.exitCode=2;
 }
}
module.exports={disposition,load};
