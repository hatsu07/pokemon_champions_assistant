"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {run,validate,signature,percentile}=require("./cross_process_synthetic_probe.cjs");
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
