"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {run,summarize,checkMap,requirements,MAP}=require("./gate_a_evidence_inventory.cjs");
let report;
test("all 10 Gate A feature gaps remain unverified, no private data is counted",()=>{
 report=run();
 assert.equal(report.schema,"phase0-gate-a-evidence-inventory-v1");
 assert.deepEqual([report.gate_A,report.gate_B,report.adoption],
   ["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(report.feature_count,10);
 assert.equal(report.event_rule_count,5);
 assert.equal(report.event_case_count,18);
 const links=report.features.find(x=>x.feature==="regulation learnsets").static_groups
   .find(x=>x.group==="pokemon_link");
 assert.ok(links);
 assert.equal(links.checked,0);
 assert.equal(links.mismatched,0);
 assert.equal(links.missing,0);
 assert.equal(report.wiki_comparable_count,1);
 assert.equal(report.game_verified_cases_this_run,0);
 assert.equal(report.game_verified_checks_this_run,0);
 assert.equal(report.private_golden_read,false);
 assert.equal(report.safe_to_integrate_with_live,false);
 assert.deepEqual(report.features.map(r=>r.feature),requirements());
 for(const f of report.features){
   assert.equal(f.feature_compatibility,"UNKNOWN");
   assert.equal(f.actual_game_status,"NOT_EVALUATED_PRIVATE_GOLDEN");
   assert.equal(f.allowed_as_certain_ko_or_safe_switch_proof,false);
   assert.ok(f.risk&&f.next_evidence);
 }
});
test("Gate A feature/source mapping changes must be explicitly reviewed",()=>{
 const deleted=structuredClone(MAP);deleted.feature_map.pop();
 assert.throws(()=>checkMap(deleted),/requirements/);
 const duplicated=structuredClone(MAP);
 duplicated.feature_map[1].feature=duplicated.feature_map[0].feature;
 assert.throws(()=>checkMap(duplicated),/requirements/);
 const unknown=structuredClone(MAP);unknown.feature_map[5].rule_ids=["invented"];
 assert.throws(()=>checkMap(unknown),/unknown source/);
});
test("source drift, removed checks and fake game confirmation fail closed",()=>{
 assert.ok(report);
 const {compare}=require("./audit.cjs");
 const {main:wiki}=require("./wiki_damage_compare.cjs");
 const {main:events}=require("./champions_event_probe.cjs");
 const a=compare(),w=wiki(),e=events();
 const badEvent=structuredClone(e);badEvent.results[0].status="GAME_OBSERVED_MATCH";
 assert.throws(()=>summarize(a,w,badEvent),/event evidence/);
 const badMaster=structuredClone(a);delete badMaster.totals.move_pp;
 assert.throws(()=>summarize(badMaster,w,e),/missing static comparison/);
 const badWiki=structuredClone(w);badWiki.gate_A_calculation="CONFIRMED";
 assert.throws(()=>summarize(a,badWiki,e),/Wiki arithmetic/);
});
