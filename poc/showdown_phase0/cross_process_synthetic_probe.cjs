"use strict";
// Phase 0 synthetic only. Not a claim of real Champions equivalence.
const {spawnSync}=require("node:child_process");
const {performance}=require("node:perf_hooks");
const path=require("node:path");
const {fingerprint}=require("./state_compare.cjs");
const {pinned}=require("./worker.cjs");
const manifest=require("./representative_states_cases.json");
const RUNS=3, CASES=127;
const REQUIRED_MASTER_FILES=[
  "Pokemon_Champions_MC_move_master.csv",
  "Pokemon_Champions_MC_usable_pokemon.csv",
  "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
  "Pokemon_Champions_MC_usable_held_items.csv",
];
const SHA256=/^[a-f0-9]{64}$/;
function assertDigest(value,label){
  if(typeof value!=="string"||!SHA256.test(value))
    throw Error("missing or malformed required SHA256: "+label);
}
function assertMasterDigests(digests){
  if(!digests||typeof digests!=="object"||Array.isArray(digests)||
    Object.keys(digests).length!==REQUIRED_MASTER_FILES.length||
    REQUIRED_MASTER_FILES.some(f=>!Object.hasOwn(digests,f)))
    throw Error("required four Champions master hashes missing or unexpected");
  for(const file of REQUIRED_MASTER_FILES)assertDigest(digests[file],"master:"+file);
}
function percentile(values,q){
 if(!values.length||values.some(n=>!Number.isFinite(n)||n<0))throw Error("invalid timing samples");
 const sorted=[...values].sort((a,b)=>a-b);
 return sorted[Math.ceil(q*sorted.length)-1];
}
function signature(report){
 if(report?.schema!=="phase0-representative-synthetic-states-v1"||
    report.pinned_showdown_sha!==pinned||report.format!=="gen9championsbssregmc"||
    report.gate_A!=="UNKNOWN"||report.gate_B!=="UNSUPPORTED"||report.adoption!=="NO_GO"||
    report.evidence_class!=="SYNTHETIC_ONLY"||report.case_count!==CASES||
    report.results?.length!==CASES||report.distinct_complete_states!==CASES||
    report.distinct_tactical_states!==CASES||report.distinct_matchups!==18||
    report.distinct_mechanisms!==8||report.counts?.PASS_SYNTHETIC!==CASES||
    report.counts?.UNSUPPORTED!==0||report.representative_100_synthetic_states_met!==true)
    throw Error("synthetic result contract changed");
 assertMasterDigests(report.champions_master_sha256);
 for(const [i,c] of manifest.cases.entries()){
   const r=report.results[i];
   if(!r||c.id!==r.case_id||c.mechanism!==r.mechanism||
      c.expected_status!==r.status||
      r.branch?.source_unchanged!==true||
      r.branch?.repeated_action_identical!==true||
      r.branch?.distinct_outcome!==true)
     throw Error("synthetic case changed: "+c.id);
   for(const [name,value] of Object.entries({
     state_sha256:r.state_sha256,
     tactical_sha256:r.tactical_sha256,
     registered_team_pair_sha256:r.registered_team_pair_sha256,
     branch_a_sha256:r.branch.a_sha256,
     branch_b_sha256:r.branch.b_sha256,
     replay_a_sha256:r.branch.replay_a_sha256
   })) assertDigest(value,c.id+":"+name);
 }
 return fingerprint({masters:report.champions_master_sha256,
   cases:report.results.map(r=>[r.case_id,r.mechanism,r.state_sha256,
      r.tactical_sha256,r.registered_team_pair_sha256,
      r.branch.a_sha256,r.branch.b_sha256,r.branch.replay_a_sha256])});
}
function validate(reports,timings,pids){
 if(reports.length!==RUNS||timings.length!==RUNS||pids.length!==RUNS||
    new Set(pids).size!==RUNS||pids.some(p=>!Number.isInteger(p)||p<=0))
    throw Error("three unique native Node processes required");
 const sigs=reports.map(signature);
 if(new Set(sigs).size!==1)throw Error("cross-process synthetic replay differed");
 const keys=["prepare_ms","serialize_ms","restore_ms","branch_ms","alternative_ms","replay_ms"];
 const stage=Object.fromEntries(keys.map(k=>{
    const samples=reports.flatMap(x=>x.results.map(r=>r.timings_ms?.[k]));
    if(samples.length!==RUNS*CASES)throw Error("stage samples missing");
    return [k,{samples:samples.length,p50:percentile(samples,.5),
       p95:percentile(samples,.95),p99:percentile(samples,.99)}];
 }));
 const memories=reports.map(x=>x.memory_bytes);
 if(memories.some(m=>!Number.isFinite(m.initial_rss)||!Number.isFinite(m.final_rss)))
   throw Error("missing process memory snapshot");
 return {schema:"phase0-cross-process-synthetic-validation-v1",
    pinned_showdown_sha:pinned,format:"gen9championsbssregmc",
    platform:process.platform,node_version:process.version,
    gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
    evidence:"SYNTHETIC_ONLY",independent_os_process_count:RUNS,
    cases_per_process:CASES,total_case_invocations:RUNS*CASES,
    unique_real_game_cases:0,cross_process_signatures_equal:true,
    cross_process_sha256:sigs[0],
    process_wall_ms:{scope:"CHILD_STARTUP_AND_127_CASES_NOT_CONTAINER_STARTUP",
       samples:RUNS,p50:percentile(timings,.5),p95:percentile(timings,.95),
       p99:percentile(timings,.99),observations:timings},
    node_stage_ms:{scope:"THREE_SEPARATE_PROCESSES_SAME_FIXED_CASES",values:stage},
    process_memory:{scope:"PRE_POST_SNAPSHOTS_NOT_PEAK_OR_LEAK",
       peak_rss_measured:false,long_term_leak_tested:false,
       snapshots:memories.map(m=>({initial_rss:m.initial_rss,final_rss:m.final_rss}))},
    native_windows_node_execution:process.platform==="win32",
    windows_gui_ipc_e2e_verified:false,game_compatibility_verified:false,
    independent_ci_runs_verified:false};
}
function run(){
 if(manifest.cases.length!==CASES)throw Error("manifest drift");
 const reports=[],timings=[],pids=[];
 for(let i=0;i<RUNS;i++){
   const start=performance.now();
   const child=spawnSync(process.execPath,
     [path.join(__dirname,"representative_states_probe.cjs")],
     {cwd:__dirname,env:process.env,encoding:"utf8",timeout:120000,
      maxBuffer:32*1024*1024});
   const ms=performance.now()-start;
   if(child.error||child.status!==0||child.signal||!child.stdout||child.stderr)
      throw Error("synthetic child run failed: "+(i+1)+"/"+RUNS);
   let result;try{result=JSON.parse(child.stdout);}
   catch{throw Error("synthetic child returned invalid JSON");}
   reports.push(result);timings.push(ms);pids.push(child.pid);
 }
 return validate(reports,timings,pids);
}
if(require.main===module){try{process.stdout.write(JSON.stringify(run())+"\n");}
  catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}}
module.exports={run,validate,signature,percentile,assertDigest,assertMasterDigests,REQUIRED_MASTER_FILES,RUNS,CASES};
