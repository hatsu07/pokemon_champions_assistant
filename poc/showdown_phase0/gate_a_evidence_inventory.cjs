"use strict";
// Phase 0 only. Aggregating independent source contracts does NOT certify Champions.
const fs=require("node:fs"),path=require("node:path");
const {compare}=require("./audit.cjs");
const {main:wiki}=require("./wiki_damage_compare.cjs");
const {main:events}=require("./champions_event_probe.cjs");
const MAP=require("./gate_a_evidence_map.json");
const CONTRACTS=require("./champions_rule_contracts.json");
const EVENT_BASELINE=require("./champions_event_baseline.json");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1",FORMAT="gen9championsbssregmc";
function requirements(){
 return fs.readFileSync(path.join(__dirname,"golden_gate_requirements.csv"),"utf8")
  .trim().split(/\r?\n/).slice(1).map(s=>s.split(","))
  .filter(row=>row[0]==="A").map(row=>row[1]);
}
function checkMap(map=MAP,rows=requirements()){
 const ids=new Set(CONTRACTS.rules.map(r=>r.id));
 if(map.schema!=="phase0-gate-a-evidence-map-v1"||map.regulation!=="M-C"||
    rows.length!==10||new Set(rows).size!==10||map.feature_map?.length!==10||
    JSON.stringify(rows)!==JSON.stringify(map.feature_map.map(r=>r.feature))||
    CONTRACTS.schema!=="champions-source-rule-contracts-v1"||
    CONTRACTS.regulation!=="M-C"||CONTRACTS.rules.length!==5||ids.size!==5)
    throw Error("Gate A source requirements or source contract map changed");
 for(const row of map.feature_map)
   if(!Array.isArray(row.static_groups)||!Array.isArray(row.rule_ids)||
      typeof row.wiki_damage!=="boolean"||!row.risk||!row.next_evidence||
      new Set(row.static_groups).size!==row.static_groups.length||
      new Set(row.rule_ids).size!==row.rule_ids.length||
      row.rule_ids.some(id=>!ids.has(id)))
     throw Error("Gate A mapping has unknown source or incomplete gap");
 return true;
}
function checkEvidence(a,w,e,map=MAP){
 if(a?.status!=="DIAGNOSTIC_ONLY"||a.upstream_sha!==PIN||a.format!==FORMAT||
    a.classification_status!=="UNVERIFIED_HYPOTHESES; NO_CHAMPIONS_GOLDEN"||
    !a.totals||Object.keys(a.master_sha256||{}).length!==4||
    a.mismatch_count!==a.mismatch_details?.length)
    throw Error("static source evidence contract changed");
 if(w?.pinned_sha!==PIN||w.format!==FORMAT||w.scope!=="OFFLINE_DIAGNOSTIC_ONLY"||
    w.gate_A_calculation!=="UNKNOWN"||w.gate_B_reconstruction!=="UNSUPPORTED"||
    w.result_count!==w.results?.length||w.result_count<1||
    w.mismatch_count!==w.results.filter(x=>x.status==="WIKI_SPEC_MISMATCH").length||
    w.results.some(x=>!["WIKI_SPEC_MATCH","WIKI_SPEC_MISMATCH"].includes(x.status)))
    throw Error("Wiki arithmetic evidence contract changed");
 if(e?.schema!=="champions-showdown-event-diagnostics-v1"||e.pinned_sha!==PIN||
    e.format!==FORMAT||e.gate_A!=="UNKNOWN"||e.gate_B!=="UNSUPPORTED"||
    e.adoption!=="NO_GO"||e.results?.length!==EVENT_BASELINE.cases.length||
    EVENT_BASELINE.pinned_sha!==PIN||
    EVENT_BASELINE.cases.some((row,i)=>["id","rule_id","status","observation_basis"]
      .some(field=>row[field]!==e.results[i]?.[field]))||
    new Set(e.results.map(x=>x.id)).size!==e.results.length)
    throw Error("Champions source event evidence changed from reviewed baseline");
 for(const f of map.feature_map)for(const group of f.static_groups){
   const x=a.totals[group];
   // audit.cjs only creates pokemon_link when a missing learnset key is found.
   // Absence is exactly zero detected link failures, not a lost check.
   if(group==="pokemon_link"&&!x)continue;
   if(!x||!Number.isInteger(x.checked)||x.checked<=0||
      x.checked!==x.matching+x.mismatched+x.missing)
     throw Error("missing static comparison group: "+group);
 }
 return true;
}
function summarize(a,w,e,map=MAP){
 checkMap(map);checkEvidence(a,w,e,map);
 const features=map.feature_map.map(f=>{
   const groups=f.static_groups.map(group=>({group,...(a.totals[group]||{checked:0,matching:0,mismatched:0,missing:0})}));
   const disagreement=groups.reduce((n,g)=>n+g.mismatched+g.missing,0);
   const matchedEvents=e.results.filter(x=>f.rule_ids.includes(x.rule_id));
   const counts=Object.fromEntries(["WIKI_SPEC_MATCH","WIKI_SPEC_MISMATCH","INCONCLUSIVE"]
    .map(status=>[status,matchedEvents.filter(x=>x.status===status).length]));
   return {feature:f.feature,regulation:"M-C",
     static_status:disagreement?"SOURCE_CONFLICT":groups.length?"SOURCE_CHECKED_ONLY":"NO_STATIC_CHECK",
     static_groups:groups,static_disagreement_count:disagreement,
     source_rule_ids:f.rule_ids,event_evidence_counts:counts,
     wiki_arithmetic_status:f.wiki_damage?(w.mismatch_count?"WIKI_SPEC_MISMATCH":"WIKI_SPEC_MATCH"):"NOT_APPLICABLE",
     actual_game_status:"NOT_EVALUATED_PRIVATE_GOLDEN",
     feature_compatibility:"UNKNOWN",risk:f.risk,next_evidence:f.next_evidence,
     allowed_as_certain_ko_or_safe_switch_proof:false};
 });
 return {schema:"phase0-gate-a-evidence-inventory-v1",pinned_sha:PIN,format:FORMAT,
   scope:"OFFLINE_SOURCE_DIAGNOSTIC_ONLY",regulation:"M-C",
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
   feature_count:features.length,master_discrepancy_count:a.mismatch_count,
   wiki_comparable_count:w.result_count,wiki_mismatch_count:w.mismatch_count,
   event_rule_count:CONTRACTS.rules.length,event_case_count:e.results.length,
   event_counts:e.counts,game_verified_cases_this_run:0,game_verified_checks_this_run:0,
   private_golden_read:false,safe_to_integrate_with_live:false,all_features_confirmed:false,
   features};
}
function run(){return summarize(compare(),wiki(),events());}
if(require.main===module){
 try{process.stdout.write(JSON.stringify(run())+"\n");}
 catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2;}
}
module.exports={run,summarize,checkMap,checkEvidence,requirements,MAP};
