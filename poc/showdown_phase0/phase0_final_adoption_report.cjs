"use strict";
// Phase 0 acceptance evidence classification. Never imports game LIVE modules.
// This is a CONDITIONAL report, not an approval engine for production.
const fs=require("node:fs");
const GATE_A_MAP=require("./gate_a_evidence_map.json");
const GATE_B_MANIFEST=require("./gate_b_cases.json");
const PARTIAL_MANIFEST=require("./partial_observation_cases.json");
const REPRESENTATIVE_MANIFEST=require("./representative_states_cases.json");
const STAGES=["prepare_ms","serialize_ms","restore_ms","branch_ms","alternative_ms","replay_ms"];
const B_STATUSES=["PASS_SYNTHETIC","STATE_OR_QUEUE_MISMATCH","LOG_ONLY_MISMATCH","INCONCLUSIVE","UNSUPPORTED"];
const PARTIAL_STATUSES=["CONSISTENT_HYPOTHESES","INCONCLUSIVE","UNSUPPORTED","SYNTHETIC_TEST_PASS","CONTRADICTORY_OBSERVATION"];
function percentile(values,q){
 const sorted=[...values].sort((a,b)=>a-b);
 return sorted[Math.ceil(q*sorted.length)-1];
}
function validTiming(n){return Number.isFinite(n)&&n>=0;}
function checkDistribution(stats,values,label,withMax=false){
 requireValue(Array.isArray(values)&&values.length>0&&values.every(validTiming),
   label+" raw timing observations");
 requireValue(stats&&stats.samples===values.length&&
   ["p50","p95","p99"].every((k,i)=>stats[k]===percentile(values,[.5,.95,.99][i]))&&
   stats.p50<=stats.p95&&stats.p95<=stats.p99&&
   (!withMax||stats.max===Math.max(...values)),
   label+" percentile, ordering or sample count mismatch");
}
function exactManifestResults(report,manifest,label,check){
 const rows=report.results,expected=manifest.cases;
 requireValue(Array.isArray(rows)&&rows.length===expected.length,
   label+" individual case count");
 for(let i=0;i<expected.length;i++){
  const row=rows[i],spec=expected[i];
  requireValue(row&&row.case_id===spec.id&&row.mechanism===spec.mechanism&&
    row.status===spec.expected_status&&check(row,spec),
    label+" individual case ID/mechanism/status drift: "+spec.id);
 }
 requireValue(new Set(rows.map(x=>x.case_id)).size===expected.length&&
   new Set(rows.map(x=>x.mechanism)).size===report.mechanism_count,
   label+" duplicate case or mechanism count");
}
function checkCounts(counts,rows,statuses,phase){
 requireValue(counts&&Object.keys(counts).sort().join(",")===statuses.slice().sort().join(",")&&
   statuses.every(status=>counts[status]===rows.filter(x=>
     (!phase||x.phase===phase)&&x.status===status).length),
   "individual results disagree with "+(phase||"all")+" summary counts");
}

const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const FORMAT="gen9championsbssregmc";
const REGULATION="M-C";
const INPUTS=Object.freeze([
 ["gate_a","phase0-gate-a-evidence.json"],
 ["gate_b","phase0-gate-b.json"],
 ["partial","phase0-partial-observation.json"],
 ["representative","phase0-representative-states.json"],
 ["cross_process","phase0-cross-process.json"]
]);
function failed(label){throw Error("PHASE0_EVIDENCE_INVALID: "+label);}
function requireValue(cond,label){if(!cond)failed(label);}
function pinned(r,id,shaKey){
 requireValue(r&&typeof r==="object"&&!Array.isArray(r),id+" absent");
 requireValue(r[shaKey]===PIN&&r.format===FORMAT,id+" pinned SHA/format");
 requireValue(r.gate_A==="UNKNOWN"&&r.gate_B==="UNSUPPORTED"&&r.adoption==="NO_GO",
  id+" historical gates must remain unsafe");
}
function evaluate(e){
 requireValue(e&&Object.keys(e).sort().join(",")===
   ["gate_a","gate_b","partial","representative","cross_process"].sort().join(","),
   "five exact diagnostic artifacts required");
 const a=e.gate_a,b=e.gate_b,partial=e.partial,rep=e.representative,cross=e.cross_process;
 pinned(a,"gate_a","pinned_sha");pinned(b,"gate_b","pinned_showdown_sha");
 pinned(partial,"partial","pinned_showdown_sha");
 pinned(rep,"representative","pinned_showdown_sha");
 pinned(cross,"cross_process","pinned_showdown_sha");
 requireValue(a.schema==="phase0-gate-a-evidence-inventory-v1"&&
   a.scope==="OFFLINE_SOURCE_DIAGNOSTIC_ONLY"&&a.regulation===REGULATION&&
   a.feature_count===10&&Array.isArray(a.features)&&a.features.length===10&&
   new Set(a.features.map(x=>x.feature)).size===10&&
   GATE_A_MAP.schema==="phase0-gate-a-evidence-map-v1"&&
   GATE_A_MAP.feature_map.length===10&&
   JSON.stringify(a.features.map(x=>x.feature))===
    JSON.stringify(GATE_A_MAP.feature_map.map(x=>x.feature))&&
   a.features.every(x=>x.regulation===REGULATION)&&
   a.features.every(x=>x.feature_compatibility==="UNKNOWN"&&
     x.actual_game_status==="NOT_EVALUATED_PRIVATE_GOLDEN"&&
     x.allowed_as_certain_ko_or_safe_switch_proof===false)&&
   a.known_modifier_source_conflicts===1&&a.known_modifier_roll_discrepancies===4&&
   a.damage_rounding_executed_synthetic_turns===16&&
   a.damage_rounding_source_discriminating_rolls===4&&
   a.damage_rounding_game_verified_turns===0&&
   a.safe_to_integrate_with_live===false&&a.private_golden_read===false&&
   a.game_verified_cases_this_run===0&&a.game_verified_checks_this_run===0,
   "gate_a source evidence and known discrepancy boundaries");
 requireValue(b.schema==="phase0-gate-b-diagnostics-v1"&&
   b.case_count===14&&b.mechanism_count===14&&
   b.unique_input_state_count===11&&b.counts?.B1?.PASS_SYNTHETIC===12&&
   b.counts?.B2?.INCONCLUSIVE===2&&b.counts?.B2?.PASS_SYNTHETIC===0&&
   b.representative_100_cases_met===false,
   "gate_b synthetic complete vs incomplete observation boundary");
 exactManifestResults(b,GATE_B_MANIFEST,"gate_b",
  (row,spec)=>row.phase===spec.phase&&row.evidence_class===
   (spec.phase==="B1"?"SYNTHETIC":"SYNTHETIC_PARTIAL_OBSERVATION")&&
   row.pinned_showdown_sha===PIN);
 requireValue(b.counts&&Object.keys(b.counts).sort().join(",")==="B1,B2",
   "gate_b phase aggregate names");
 checkCounts(b.counts.B1,b.results,B_STATUSES,"B1");
 checkCounts(b.counts.B2,b.results,B_STATUSES,"B2");
 requireValue(new Set(b.results.map(x=>x.input_state_sha256)).size===
  b.unique_input_state_count,"gate_b unique input state hashes");
 requireValue(partial.schema==="phase0-partial-observation-diagnostics-v1"&&
   partial.case_count===26&&partial.mechanism_count===26&&
   partial.representative_100_cases_met===false&&
   Array.isArray(partial.results)&&partial.results.length===26&&
   partial.results.every(x=>x.unique_truth_restoration===false&&
    x.complete_snapshot_restorable_from_observation===false),
   "partial observation cannot reconstruct a unique actual truth");
 exactManifestResults(partial,PARTIAL_MANIFEST,"partial_observation",
  row=>row.evidence_class==="SYNTHETIC_ONLY"&&
   row.pinned_showdown_sha===PIN&&row.format===FORMAT);
 checkCounts(partial.counts,partial.results,PARTIAL_STATUSES);
 requireValue(partial.synthetic_condition_pass_count===partial.results.filter(x=>
   ["CONSISTENT_HYPOTHESES","SYNTHETIC_TEST_PASS"].includes(x.status)).length,
   "partial synthetic condition summary disagrees with individual rows");
 requireValue(rep.schema==="phase0-representative-synthetic-states-v1",
   "representative synthetic schema");
 requireValue(rep.case_count===127&&rep.counts?.PASS_SYNTHETIC===127&&
   rep.representative_100_synthetic_states_met===true&&
   rep.tactical_hash_scope==="ACTIVE_PAIR_COARSE_PROJECTION_ONLY"&&
   rep.timing_scope==="SINGLE_LINUX_CONTAINER_WARM_NODE_INTERNAL_STAGES_ONLY"&&
   rep.native_game_verification===false&&rep.windows_e2e_verified===false&&
   rep.memory_bytes?.peak_rss_measured===false&&
   rep.memory_bytes?.long_running_leak_tested===false,
   "representative 127 cases are synthetic Linux-only warm measures");
 exactManifestResults(rep,REPRESENTATIVE_MANIFEST,"representative",
  row=>row.evidence_class==="SYNTHETIC_ONLY"&&
   row.branch?.source_unchanged===true&&
   row.branch?.repeated_action_identical===true&&
   row.branch?.distinct_outcome===true);
 checkCounts(rep.counts,rep.results,["PASS_SYNTHETIC","UNSUPPORTED"]);
 requireValue(rep.distinct_mechanisms===REPRESENTATIVE_MANIFEST.mechanisms.length&&
   JSON.stringify([...new Set(rep.results.map(x=>x.mechanism))])===
     JSON.stringify(REPRESENTATIVE_MANIFEST.mechanisms),
   "representative mechanism coverage drift");
 requireValue(cross.schema==="phase0-cross-process-synthetic-validation-v1"&&
   cross.independent_os_process_count===3&&cross.cases_per_process===127&&
   cross.total_case_invocations===381&&cross.cross_process_signatures_equal===true&&
   cross.unique_real_game_cases===0&&cross.game_compatibility_verified===false&&
   cross.windows_gui_ipc_e2e_verified===false&&
   cross.independent_ci_runs_verified===false&&
   cross.process_memory?.peak_rss_measured===false&&
   cross.process_memory?.long_term_leak_tested===false,
   "three same-OS processes do not prove independent CI runs or E2E");
 const latency=cross.process_wall_ms;
 requireValue(latency?.scope==="CHILD_STARTUP_AND_127_CASES_NOT_CONTAINER_STARTUP",
   "synthetic process timing scope");
 checkDistribution(latency,latency.observations,"three Node child-process wall times");
 const stages=rep.timing_ms;
 requireValue(stages&&Object.keys(stages).sort().join(",")===
   STAGES.slice().sort().join(","),"representative six timing stages");
 for(const key of STAGES){
   const values=rep.results.map(x=>x.timings_ms?.[key]);
   checkDistribution(stages[key],values,"representative "+key,true);
   requireValue(stages[key].samples===rep.case_count,
     "representative stage needs all 127 case observations: "+key);
 }
 requireValue(cross.node_stage_ms?.scope==="THREE_SEPARATE_PROCESSES_SAME_FIXED_CASES"&&
   cross.node_stage_ms.values&&
   Object.keys(cross.node_stage_ms.values).sort().join(",")===
    STAGES.slice().sort().join(",")&&
   STAGES.every(k=>{
    const x=cross.node_stage_ms.values[k];
    return x?.samples===381&&
      ["p50","p95","p99"].every(y=>validTiming(x[y]))&&
      x.p50<=x.p95&&x.p95<=x.p99;
   }),"cross-process stage quantiles and 381 sample counts");
 const aFeatures=a.features.map(x=>({
   feature:x.feature,source_validation:x.actual_game_status,
   feature_compatibility:x.feature_compatibility,
   certain_ko_or_safe_switch_approved:false,
   source_disagreements:x.static_disagreement_count||0
 }));
 return {
  schema:"phase0-final-adoption-evidence-v1",pinned_sha:PIN,
  format:FORMAT,regulation:REGULATION,scope:"OFFLINE_SYNTHETIC_AND_SOURCE_REPORT_ONLY",
  source_artifacts:INPUTS.map(([key,file])=>({key,file,read_and_validated:true})),
  gates:{A:"UNKNOWN",B:"UNSUPPORTED",phase1_adoption:"NO_GO",
   full_engine_integration_permitted:false,live_recommendations_permitted:false,
   reason:"Neither broad Champions-game calculation parity nor partial real-game midbattle reconstruction is proven"},
  gate_A:{feature_count:10,features:aFeatures,
   evidence_class:"SOURCE_AUDITED_AND_PINNED_SYNTHETIC_NOT_FULL_GAME",
   game_verified_cases_in_ci:0,game_verified_checks_in_ci:0,
   device_golden_read:false,known_source_method_disagreements:1,
   discriminating_damage_rolls:4,synthetic_executed_damage_turns:16,
   risk:"Unknown damage/speed/type/history must not yield guaranteed KO or safe switch"},
  gate_B:{full_synthetic_snapshots_passed:12,partial_projection_cases:2,
   partial_projection_status:"INCONCLUSIVE",
   distinct_full_snapshot_inputs:11,independent_partial_hypothesis_cases:26,
   reconstructs_real_game_partial_state:false,
   risk:"Hidden PP/items/ability, prior Mega history, queue and RNG cannot be recreated from visible evidence"},
  performance:{synthetic_representative_state_cases:127,
   synthetic_conditions_met:true,
   cross_process_runs:3,cases_per_process:127,
   cross_process_output_equal:true,
   timings:{linux_warm_stage_ms:stages,
      single_os_child_startup_plus_127_cases_ms:{
       samples:latency.samples,p50:latency.p50,p95:latency.p95,p99:latency.p99}},
   p95_3000ms_product_e2e_met:false,
   p95_product_e2e_measured:false,
   windows_app_included_node_worker_measured:false,
   peak_rss_measured:false,long_term_leak_tested:false,
   timing_note:"Local probe timings are observations, not Windows UI latency or speed guarantee"},
  deployment:{pinned_upstream_sha:PIN,linux_isolated_build:true,
   windows_native_node_poc:"SEPARATE_CI_LOG_VERIFICATION_REQUIRED",
   windows_onedir_offline_worker:"NOT_TESTED",
   windows_ipc_worker_recovery:"NOT_TESTED",
   android_ocr_to_gui_e2e:"NOT_TESTED",
   upstream_license:"MIT_REPORTED_IN_REPO_AND_LICENSE_PRESENT_AT_BUILD",
   npm_dependency_license_audit:"NOT_TESTED",
   sbom_and_redistribution_notices:"NOT_TESTED",
   build_container_is_product:false},
  real_game_evidence:{ci_input_game_observations:0,private_golden_read:false,
   issue_139_reported_local_checks:11,issue_139_reported_local_cases:6,
   provenance:"ISSUE_139_USER_VERIFIED_LOCAL_HISTORICAL_SUMMARY_NOT_REEXECUTED_IN_CI",
   independent_battles_not_established:true,
   limitations:"Only Azumarill stat/PP and one Knock Off critical observed hit; not broad game compatibility"},
  alternatives:[
   {id:"full_showdown",recommendation:"DO_NOT_ADOPT",blocking:"Gate A/B + Windows distribution/E2E"},
   {id:"limited_offline",recommendation:"RESEARCH_ONLY",blocking:"Define independently game-verified scope; no LIVE output"},
   {id:"current_live",recommendation:"KEEP_UNCHANGED",blocking:"Continue recording real battles; no engine switch"}
  ],
  next_evidence:[
   "User-verified high-information integer HP damage cases and high-risk mechanics",
   "Partial game observation provenance and fail-closed reconstruction for unknown fields",
   "Windows onedir Node runtime, local IPC and crash/timeout/revision tests",
   "Dependency license inventory/SBOM and offline redistributable notices",
   "End-to-end Android mirror OCR to desktop recommendation latency measured with n/p95/p99"
  ],
  phase0_ready_to_close:false,
  evidence_does_not_enable_phase1:true
 };
}
function load(files){
 const inputs={};
 for(const [k] of INPUTS){
   const file=files[k];
   requireValue(typeof file==="string"&&file.length>0,"file missing: "+k);
   try{inputs[k]=JSON.parse(fs.readFileSync(file,"utf8"));}
   catch(e){failed(k+" unreadable: "+e.message)}
 }
 return evaluate(inputs);
}
if(require.main===module){
 try{
  const args=process.argv.slice(2);requireValue(args.length===INPUTS.length,"five positional JSON files");
  const filenames=Object.fromEntries(INPUTS.map(([key],i)=>[key,args[i]]));
  const report=load(filenames);
  process.stdout.write(JSON.stringify(report)+"\n");
 }catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2}
}
module.exports={evaluate,load,PIN,FORMAT,INPUTS};
