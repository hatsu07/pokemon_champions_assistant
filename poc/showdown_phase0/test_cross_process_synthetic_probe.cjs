"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {run,validate,signature,percentile,REQUIRED_MASTER_FILES}=
  require("./cross_process_synthetic_probe.cjs");
const manifest=require("./representative_states_cases.json");
test("127 fixed synthetic states independently replay in 3 fresh Node processes",()=>{
 const r=run();
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.independent_os_process_count,3);
 assert.equal(r.cases_per_process,127);
 assert.equal(r.total_case_invocations,381);
 assert.equal(r.unique_real_game_cases,0);
 assert.equal(r.cross_process_signatures_equal,true);
 assert.equal(r.game_compatibility_verified,false);
 assert.equal(r.windows_gui_ipc_e2e_verified,false);
 assert.equal(r.independent_ci_runs_verified,false);
 for(const x of Object.values(r.node_stage_ms.values)){
   assert.equal(x.samples,381);
   assert.ok(x.p50<=x.p95&&x.p95<=x.p99);
 }
});
test("reject missing cases, nonunique process IDs and bad timings",()=>{
 assert.throws(()=>signature({}),/contract changed/);
 assert.throws(()=>validate([{},{}],[1,2],[3,4]),/unique native Node processes/);
 assert.throws(()=>validate([{}, {}, {}],[1,2,3],[4,4,5]),/unique native Node processes/);
 assert.deepEqual([.5,.95,.99].map(x=>percentile([1,2,3],x)),[2,3,3]);
 assert.throws(()=>percentile([1,-1],.5),/invalid timing/);
});

test("missing or malformed required source, team and branch SHA must fail independently",()=>{
 const SHA="a".repeat(64);
 const make=()=>({
   schema:"phase0-representative-synthetic-states-v1",
   pinned_showdown_sha:"ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
   format:"gen9championsbssregmc",gate_A:"UNKNOWN",
   gate_B:"UNSUPPORTED",adoption:"NO_GO",evidence_class:"SYNTHETIC_ONLY",
   case_count:127,distinct_complete_states:127,distinct_tactical_states:127,
   distinct_matchups:18,distinct_mechanisms:8,
   counts:{PASS_SYNTHETIC:127,UNSUPPORTED:0},
   representative_100_synthetic_states_met:true,
   champions_master_sha256:Object.fromEntries(REQUIRED_MASTER_FILES.map(f=>[f,SHA])),
   results:manifest.cases.map(c=>({
     case_id:c.id,mechanism:c.mechanism,status:c.expected_status,
     state_sha256:SHA,tactical_sha256:SHA,registered_team_pair_sha256:SHA,
     branch:{source_unchanged:true,repeated_action_identical:true,
       distinct_outcome:true,a_sha256:SHA,b_sha256:SHA,replay_a_sha256:SHA}
   }))
 });
 assert.match(signature(make()),/^[a-f0-9]{64}$/);
 for(const name of REQUIRED_MASTER_FILES){
   const absent=make();delete absent.champions_master_sha256[name];
   assert.throws(()=>signature(absent),/required four Champions master hashes/);
   const bad=make();bad.champions_master_sha256[name]=SHA.slice(1);
   assert.throws(()=>signature(bad),/missing or malformed required SHA256/);
 }
 const extra=make();extra.champions_master_sha256.unexpected=SHA;
 assert.throws(()=>signature(extra),/required four Champions master hashes/);
 for(const field of ["state_sha256","tactical_sha256",
     "registered_team_pair_sha256"]){
   const absent=make();delete absent.results[2][field];
   assert.throws(()=>signature(absent),/required SHA256/);
   const malformed=make();malformed.results[3][field]="F".repeat(64);
   assert.throws(()=>signature(malformed),/required SHA256/);
 }
 for(const field of ["a_sha256","b_sha256","replay_a_sha256"]){
   const absent=make();delete absent.results[1].branch[field];
   assert.throws(()=>signature(absent),/required SHA256/);
   const malformed=make();malformed.results[4].branch[field]="abcd";
   assert.throws(()=>signature(malformed),/required SHA256/);
 }
});
