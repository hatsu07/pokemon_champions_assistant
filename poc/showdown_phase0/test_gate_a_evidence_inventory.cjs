"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {run,summarize,checkMap,checkModifierEvidence,checkTurnOrderEvidence,
 requirements,KNOWN_AUDIT_GROUPS,MAP}=require("./gate_a_evidence_inventory.cjs");
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
 assert.equal(report.turn_order_source_matches,9);
 assert.equal(report.turn_order_inconclusive_ties,1);
 assert.equal(report.turn_order_scope,"PINNED_SYNTHETIC_EXECUTED_TURNS_NOT_DEVICE");
 const priority=report.features.find(x=>x.feature==="priority");
 assert.equal(priority.turn_order_source_matches,9);
 assert.equal(priority.turn_order_inconclusive_ties,1);
 assert.equal(priority.turn_order_scope,"PINNED_SYNTHETIC_EXECUTED_TURNS_NOT_DEVICE");
 assert.equal(priority.feature_compatibility,"UNKNOWN");
 assert.equal(priority.actual_game_status,"NOT_EVALUATED_PRIVATE_GOLDEN");
 assert.equal(report.wiki_mismatch_count,0);
 assert.equal(report.modifier_source_status,"REVIEWED_SECONDARY_SOURCE_DIVERGENCE");
 assert.equal(report.known_modifier_source_conflicts,1);
 assert.equal(report.known_modifier_roll_discrepancies,4);
 assert.equal(report.modifier_comparison_scope,"SECONDARY_SOURCE_VS_PINNED_SHOWDOWN_NOT_DEVICE");
 const damage=report.features.find(x=>x.feature==="damage RNG and critical");
 assert.equal(damage.wiki_arithmetic_status,"WIKI_SPEC_MATCH"); // old single example is still correct
 assert.equal(damage.modifier_source_status,"REVIEWED_SECONDARY_SOURCE_DIVERGENCE");
 assert.equal(damage.known_modifier_source_conflicts,1);
 assert.equal(damage.known_modifier_roll_discrepancies,4);
 assert.equal(damage.modifier_comparison_scope,"SECONDARY_SOURCE_VS_PINNED_SHOWDOWN_NOT_DEVICE");
 const mapped=new Set(report.features.flatMap(x=>x.static_groups.map(g=>g.group)));
 assert.deepEqual([...mapped].sort(),[...KNOWN_AUDIT_GROUPS].sort());
 const moves=report.features.find(x=>x.feature==="move power and PP");
 assert.ok(moves.static_groups.some(x=>x.group==="move_type"));
 assert.ok(moves.static_groups.some(x=>x.group==="move_category"));
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

test("no known static audit group can silently disappear from feature map",()=>{
 for(const name of ["move_type","move_category"]){
   const missing=structuredClone(MAP);
   missing.feature_map.forEach(row=>{
     row.static_groups=row.static_groups.filter(g=>g!==name);
   });
   assert.throws(()=>checkMap(missing),/UNMAPPED_AUDIT_GROUP/);
 }
 const unknown=structuredClone(MAP);
 unknown.feature_map[1].static_groups.push("new_unreviewed_group");
 assert.throws(()=>checkMap(unknown),/unreviewed static audit group/);
});
test("move type/category drift is shown as SOURCE_CONFLICT instead of silently dropped",()=>{
 const {compare}=require("./audit.cjs");
 const {main:wiki}=require("./wiki_damage_compare.cjs");
 const {main:events}=require("./champions_event_probe.cjs");
 const a=compare(),w=wiki(),e=events();
 for(const group of ["move_type","move_category"]){
   const drift=structuredClone(a);
   assert.ok(drift.totals[group].matching>0);
   drift.totals[group].matching--;
   drift.totals[group].mismatched++;
   drift.mismatch_count++;
   drift.mismatch_details.push({
     group,key:"SYNTHETIC_NEGATIVE_TEST_NOT_GAME_EVIDENCE",
     field:"type_or_category",expected:"TEST_EXPECTED",actual:"TEST_DIFFERENT"
   });
   const report=summarize(drift,w,e);
   const moves=report.features.find(f=>f.feature==="move power and PP");
   assert.equal(moves.static_status,"SOURCE_CONFLICT");
   assert.equal(moves.static_disagreement_count,1);
   assert.equal(moves.feature_compatibility,"UNKNOWN");
   assert.equal(moves.allowed_as_certain_ko_or_safe_switch_proof,false);
 }
 const newGroup=structuredClone(a);
 newGroup.totals.unreviewed_category={checked:1,matching:0,mismatched:1,missing:0};
 newGroup.mismatch_count++;
 newGroup.mismatch_details.push({
   group:"unreviewed_category",key:"SYNTHETIC_NEGATIVE_TEST",
   field:"unreviewed",expected:"x",actual:"y"
 });
 assert.throws(()=>summarize(newGroup,w,e),/UNMAPPED_AUDIT_GROUP/);
});

test("modifier conflict cannot disappear, grow, or become fake device evidence in Gate A",()=>{
 const {main:modifier}=require("./champions_damage_modifier_probe.cjs");
 const original=modifier();
 assert.deepEqual(checkModifierEvidence(original),{conflicts:1,rollDifferences:4});
 const {compare}=require("./audit.cjs");
 const {main:wiki}=require("./wiki_damage_compare.cjs");
 const {main:events}=require("./champions_event_probe.cjs");
 const a=compare(),w=wiki(),e=events();
 assert.throws(()=>summarize(a,w,e,MAP,null),/modifier source evidence/);
 const changes=[
   m=>{m.game_verified_check_count=1;},
   m=>{m.gate_A="CONFIRMED";},
   m=>{m.source_divergence_count=0;},
   m=>{m.counts.WIKI_SPEC_MISMATCH=0;},
   m=>{m.results[8].difference_count=0;},
   m=>{m.results[8].first_differences[0].pinned_showdown++;},
   m=>{m.results[8].odd_rounding_witnesses[0].pinned_damage++;},
   m=>{m.results[0].status="WIKI_SPEC_MISMATCH";},
   m=>{m.results[0].difference_count=1;},
   m=>{m.results.pop();},
   m=>{m.results[8].reviewed_source_discrepancy=null;}
 ];
 for(const mutate of changes){
   const changed=structuredClone(original);
   mutate(changed);
   assert.throws(()=>checkModifierEvidence(changed),
     /modifier (source|known)|unexpected modifier/);
   assert.throws(()=>summarize(a,w,e,MAP,changed),
     /modifier (source|known)|unexpected modifier/);
 }
 const valid=summarize(a,w,e,MAP,original);
 assert.equal(valid.known_modifier_roll_discrepancies,4);
 assert.equal(valid.game_verified_checks_this_run,0);
 assert.deepEqual([valid.gate_A,valid.gate_B,valid.adoption],
   ["UNKNOWN","UNSUPPORTED","NO_GO"]);
});

test("Gate A priority shows synthetic source ordering but never promotes tie or game evidence",()=>{
 const {run:order}=require("./champions_turn_order_probe.cjs");
 const r=order();
 assert.deepEqual(checkTurnOrderEvidence(r),{matches:9,ties:1});
 const {compare}=require("./audit.cjs");
 const {main:wiki}=require("./wiki_damage_compare.cjs");
 const {main:events}=require("./champions_event_probe.cjs");
 const {main:modifier}=require("./champions_damage_modifier_probe.cjs");
 const a=compare(),w=wiki(),e=events(),m=modifier();
 const report=summarize(a,w,e,MAP,m,r);
 assert.deepEqual([report.gate_A,report.gate_B,report.adoption],
   ["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(report.turn_order_source_matches,9);
 assert.equal(report.turn_order_inconclusive_ties,1);
 assert.equal(report.game_verified_checks_this_run,0);
 assert.equal(report.safe_to_integrate_with_live,false);
 assert.throws(()=>summarize(a,w,e,MAP,m,null),/turn-order synthetic source evidence/);
 const changes=[
   o=>{o.counts.SOURCE_SPEC_MATCH=10;},
   o=>{o.counts.INCONCLUSIVE_SPEED_TIE=0;},
   o=>{o.game_verified_turn_count=1;},
   o=>{o.official_or_device_confirmed=true;},
   o=>{o.live_use_approved=true;},
   o=>{o.gate_A="CONFIRMED";},
   o=>{o.results[6].status="SOURCE_SPEC_MATCH";},
   o=>{o.results[6].expected_first="p2";},
   o=>{o.results[6].comparison_scope="EXECUTED_PINNED_TURN_VS_SECONDARY_SOURCE";},
   o=>{o.results[0].executed_moves.p1.name="Protect";},
   o=>{o.results[0].executed_moves.p1.priority=4;},
   o=>{o.results[1].observed_first="p1";},
   o=>{o.results[0].p1_effective_speed++;},
   o=>{o.results.pop();}
 ];
 for(const mutate of changes){
   const modified=structuredClone(r);
   mutate(modified);
   assert.throws(()=>checkTurnOrderEvidence(modified),
     /turn-order (synthetic source evidence|reviewed source case|source status counts)/);
   assert.throws(()=>summarize(a,w,e,MAP,m,modified),
     /turn-order (synthetic source evidence|reviewed source case|source status counts)/);
 }
});
