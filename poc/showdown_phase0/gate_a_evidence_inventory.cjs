"use strict";
// Phase 0 only. Aggregating independent source contracts does NOT certify Champions.
const fs=require("node:fs"),path=require("node:path");
const {compare}=require("./audit.cjs");
const {main:wiki}=require("./wiki_damage_compare.cjs");
const {main:events}=require("./champions_event_probe.cjs");
const {run:turnOrder}=require("./champions_turn_order_probe.cjs");
const {run:paralysisOrder}=require("./champions_paralysis_probe.cjs");
const {run:weatherSpeed}=require("./champions_weather_speed_probe.cjs");
const {run:executedRounding}=require("./champions_damage_rounding_executed_probe.cjs");
const {checkExecutedRoundingEvidence}=require("./champions_damage_rounding_gate_a_contract.cjs");
let cachedExecutedRounding=null;
function executedRoundingOnce(){
 return cachedExecutedRounding||(cachedExecutedRounding=executedRounding());
}
const {checkWeatherEvidence}=require("./champions_weather_gate_a_contract.cjs");
let cachedWeather=null;
function weatherOnce(){return cachedWeather||(cachedWeather=weatherSpeed());}
const {checkParalysisEvidence}=require("./champions_paralysis_gate_a_contract.cjs");
let cachedParalysis=null;
function paralysisOnce(){return cachedParalysis||(cachedParalysis=paralysisOrder());}
const {LEDGER:TURN_LEDGER,referenceCase}=require("./champions_turn_order_reference.cjs");
const {moveKey}=require("./champions_move_key.cjs");
const {main:modifier,REVIEWED_CONTRACTS,REVIEWED_SOURCE_URLS,
  REVIEWED_PINNED_DIVERGENCE,WITNESS_ID}=require("./champions_damage_modifier_probe.cjs");
const MAP=require("./gate_a_evidence_map.json");
const CONTRACTS=require("./champions_rule_contracts.json");
const EVENT_BASELINE=require("./champions_event_baseline.json");
const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1",FORMAT="gen9championsbssregmc";
// Reviewed list of ALL comparison groups emitted by audit.cjs::compare/check.
// pokemon_link is discrepancy-only, so it may be absent when there is no missing link.
// A new audit group must be consciously added here AND assigned to a Gate A feature.
const KNOWN_AUDIT_GROUPS=new Set([
 "move_coverage","move_type","move_category","move_power","move_priority",
 "move_pp","move_accuracy","pokemon_link","species_coverage",
 "species_base_stats","species_types","learnset_membership","item_coverage"
]);
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
 const mapped=new Set();
 for(const row of map.feature_map){
   if(!Array.isArray(row.static_groups)||!Array.isArray(row.rule_ids)||
      typeof row.wiki_damage!=="boolean"||!row.risk||!row.next_evidence||
      new Set(row.static_groups).size!==row.static_groups.length||
      new Set(row.rule_ids).size!==row.rule_ids.length||
      row.rule_ids.some(id=>!ids.has(id)))
     throw Error("Gate A mapping has unknown source or incomplete gap");
   for(const group of row.static_groups){
     if(!KNOWN_AUDIT_GROUPS.has(group))
       throw Error("Gate A map contains unreviewed static audit group: "+group);
     mapped.add(group);
   }
 }
 const unmapped=[...KNOWN_AUDIT_GROUPS].filter(group=>!mapped.has(group));
 if(unmapped.length)
   throw Error("UNMAPPED_AUDIT_GROUP: "+unmapped.join(","));
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
 // Detect newly introduced groups even when their counts are all matching.
 // Without this guard a future source disagreement could silently disappear.
 const groups=Object.keys(a.totals);
 const unmapped=groups.filter(group=>!map.feature_map.some(row=>row.static_groups.includes(group)));
 if(unmapped.length)throw Error("UNMAPPED_AUDIT_GROUP: "+unmapped.join(","));
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
// This is a reviewed *secondary-source* disagreement, never evidence of device parity.
// Recompute the pinned diagnostic rather than trusting counts copied into a report.
function checkModifierEvidence(m){
 if(m?.schema!=="phase0-champions-damage-modifier-diagnostic-v1"||
    m.pinned_sha!==PIN||m.format!==FORMAT||m.regulation!=="M-C"||
    m.evidence!=="CHAMPIONS_SECONDARY_SOURCE_ARITHMETIC_NOT_DEVICE_GOLDEN"||
    m.gate_A!=="UNKNOWN"||m.gate_B!=="UNSUPPORTED"||m.adoption!=="NO_GO"||
    m.game_verified_check_count!==0||m.executed_turns!==0||
    m.private_golden_read!==false||m.live_use_approved!==false||
    m.unknown_source_divergences_accepted!==false||
    m.reviewed_source_divergence!=="PINNED_SPLIT_ROUNDING_VS_SECONDARY_COMBINED_MODIFIER"||
    m.scenario_count!==REVIEWED_CONTRACTS.length||m.individual_roll_comparisons!==144||
    m.odd_rounding_witness_count!==4||
    m.odd_rounding_witness_verified_in_pinned_getDamage!==true||
    JSON.stringify(m.source_ids)!==JSON.stringify(Object.keys(REVIEWED_SOURCE_URLS))||
    JSON.stringify(m.results?.map(r=>r.id))!==
      JSON.stringify(REVIEWED_CONTRACTS.map(r=>r.id)))
   throw Error("modifier source evidence contract changed");
 let conflicts=0,rollDifferences=0;
 for(let i=0;i<REVIEWED_CONTRACTS.length;i++){
   const expected=REVIEWED_CONTRACTS[i],r=m.results[i],odd=r.id===WITNESS_ID;
   const status=odd?"WIKI_SPEC_MISMATCH":"WIKI_SPEC_MATCH";
   if(r.status!==status||r.difference_count!==(odd?4:0)||
      JSON.stringify(r.conditions)!==JSON.stringify({
        burn:expected.burn,reflect:expected.reflect,critical:expected.critical})||
      JSON.stringify(r.source_ids)!==JSON.stringify(expected.source_ids)||
      r.source_numeric_basis!==(odd?"DERIVED_FORMULA_ONLY":"PUBLISHED_FIXTURE_AND_DERIVED_MODIFIERS")||
      r.defender!==(odd?"Blastoise":"Metagross")||
      !Array.isArray(r.first_differences)||
      r.first_differences.length!==(odd?4:0))
     throw Error("modifier source scenario drift: "+r.id);
   if(odd){
     const diffs=REVIEWED_PINNED_DIVERGENCE;
     if(r.reviewed_source_discrepancy?.status!=="REVIEWED_SECONDARY_SOURCE_DIVERGENCE"||
        r.reviewed_source_discrepancy.difference_count!==4||
        JSON.stringify(r.first_differences)!==JSON.stringify(diffs.map(x=>({
          roll_pct:x.roll_pct,reference:x.combined_rounding,
          pinned_showdown:x.split_rounding})))||
        JSON.stringify(r.odd_rounding_witnesses?.map(x=>({
          roll_pct:x.roll_pct,pre_final_modifier:x.pre_final_modifier,
          combined_rounding:x.combined_rounding,split_rounding:x.split_rounding,
          pinned_damage:x.pinned_damage})))!==
          JSON.stringify(diffs.map(x=>({...x,pinned_damage:x.split_rounding}))))
       throw Error("modifier known source discrepancy changed");
   }else if(r.reviewed_source_discrepancy!==null||
            r.odd_rounding_witnesses?.length!==0)
     throw Error("unexpected modifier source discrepancy: "+r.id);
   conflicts+=Number(r.status==="WIKI_SPEC_MISMATCH");
   rollDifferences+=r.difference_count;
 }
 if(conflicts!==1||rollDifferences!==4||
    m.source_divergence_count!==conflicts||
    m.counts?.WIKI_SPEC_MATCH!==8||m.counts?.WIKI_SPEC_MISMATCH!==conflicts)
   throw Error("modifier source discrepancy count changed");
 return {conflicts,rollDifferences};
}
// A source-only synthetic observation, NOT Champions device/Golden validation.
function checkTurnOrderEvidence(t){
 if(t?.schema!=="phase0-champions-turn-order-diagnostic-v1"||
    t.pinned_sha!==PIN||t.format!==FORMAT||t.regulation!=="M-C"||
    t.evidence!=="CHAMPIONS_SPECIFIC_SECONDARY_SOURCE_AND_FIXED_MASTER_NOT_DEVICE"||
    t.gate_A!=="UNKNOWN"||t.gate_B!=="UNSUPPORTED"||t.adoption!=="NO_GO"||
    t.game_verified_turn_count!==0||t.private_golden_read!==false||
    t.official_or_device_confirmed!==false||t.live_use_approved!==false||
    t.registered_six_member_teams!==true||t.selected_three_member_teams!==true||
    t.checked_move_priority_count!==5||
    t.scenarios!==TURN_LEDGER.cases.length||
    t.pinned_executed_turn_count!==TURN_LEDGER.cases.length||
    t.results?.length!==TURN_LEDGER.cases.length||
    JSON.stringify(t.source_ids)!==JSON.stringify(TURN_LEDGER.sources.map(x=>x.id)))
   throw Error("turn-order synthetic source evidence contract changed");
 let matches=0,ties=0,mismatches=0;
 for(let i=0;i<TURN_LEDGER.cases.length;i++){
   const c=TURN_LEDGER.cases[i],row=t.results[i],ref=referenceCase(c);
   const isTie=ref.expected_first==="UNRESOLVED_TIE";
   const status=isTie?"INCONCLUSIVE_SPEED_TIE":"SOURCE_SPEC_MATCH";
   // Validate exact names, Champions MoveKeys and priorities per side;
   // side order in the observed log varies with the first actor.
   const observedMoves={
     p1:{id:moveKey(c.p1),name:c.p1,priority:ref.p1_priority},
     p2:{id:moveKey(c.p2),name:c.p2,priority:ref.p2_priority}
   };
   if(!row||row.id!==c.id||row.expected_first!==ref.expected_first||
      row.status!==status||!["p1","p2"].includes(row.observed_first)||
      (!isTie&&row.observed_first!==ref.expected_first)||
      row.comparison_scope!==(isTie?"TIE_OUTCOME_NOT_A_SINGLE_VALUE_ASSERTION":
          "EXECUTED_PINNED_TURN_VS_SECONDARY_SOURCE")||
      row.observed_sequence?.length!==2||
      row.observed_sequence[0]!==row.observed_first||
      row.observed_sequence[1]===(row.observed_first)||
      !row.executed_moves||
      JSON.stringify(row.executed_moves.p1)!==JSON.stringify(observedMoves.p1)||
      JSON.stringify(row.executed_moves.p2)!==JSON.stringify(observedMoves.p2)||
      row.p1_base_speed!==ref.p1_base_speed||row.p2_base_speed!==ref.p2_base_speed||
      row.p1_effective_speed!==ref.p1_effective_speed||
      row.p2_effective_speed!==ref.p2_effective_speed||
      row.p1_priority!==ref.p1_priority||row.p2_priority!==ref.p2_priority||
      JSON.stringify(row.source_only_preconditions)!==
        JSON.stringify({synthetic_speed_boost:c.m_stage,
          synthetic_trick_room:c.trick_room,scarf_held:c.m_scarf}))
     throw Error("turn-order reviewed source case drift: "+c.id);
   matches+=Number(row.status==="SOURCE_SPEC_MATCH");
   ties+=Number(row.status==="INCONCLUSIVE_SPEED_TIE");
   mismatches+=Number(row.status==="SOURCE_SPEC_MISMATCH");
 }
 if(matches!==9||ties!==1||mismatches!==0||
    t.counts?.SOURCE_SPEC_MATCH!==matches||
    t.counts?.SOURCE_SPEC_MISMATCH!==mismatches||
    t.counts?.INCONCLUSIVE_SPEED_TIE!==ties)
   throw Error("turn-order source status counts changed");
 return {matches,ties};
}
function summarize(a,w,e,map=MAP,m=modifier(),t=turnOrder(),p=paralysisOnce(),weather=weatherOnce(),rounding=executedRoundingOnce()){
 checkMap(map);checkEvidence(a,w,e,map);
 const modifierCounts=checkModifierEvidence(m);
 const turnCounts=checkTurnOrderEvidence(t);
 const paralysisCounts=checkParalysisEvidence(p);
 const weatherCounts=checkWeatherEvidence(weather);
 const roundingCounts=checkExecutedRoundingEvidence(rounding);
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
     ...(f.wiki_damage?{
       modifier_source_status:"REVIEWED_SECONDARY_SOURCE_DIVERGENCE",
       known_modifier_source_conflicts:modifierCounts.conflicts,
       known_modifier_roll_discrepancies:modifierCounts.rollDifferences,
       modifier_comparison_scope:"SECONDARY_SOURCE_VS_PINNED_SHOWDOWN_NOT_DEVICE",
       modifier_diagnostic:"champions_damage_modifier_probe.cjs"
     }:{}),
     ...(f.feature==="priority"?{
       turn_order_source_matches:turnCounts.matches,
       turn_order_inconclusive_ties:turnCounts.ties,
       turn_order_scope:"PINNED_SYNTHETIC_EXECUTED_TURNS_NOT_DEVICE",
       turn_order_diagnostic:"champions_turn_order_probe.cjs"
     }:{}),
     ...(["priority","status and boosts"].includes(f.feature)?{
       paralysis_source_order_matches:paralysisCounts.matches,
       paralysis_unknown_compound_rounding:paralysisCounts.rounding_unknown,
       paralysis_scope:paralysisCounts.scope,
       paralysis_pinned_rate:"1/8_SOURCE_ONLY_NOT_DEVICE",
       paralysis_diagnostic:"champions_paralysis_probe.cjs"
     }:{}),
     ...(["weather screens hazards","priority","ability and item"].includes(f.feature)?{
       weather_source_order_matches:weatherCounts.matches,
       weather_unknown_compound_rounding:weatherCounts.rounding_unknown,
       weather_item_extension_source_agreement_unexecuted:weatherCounts.source_agreed_unexecuted,
       weather_same_kind_reapply_unexecuted:weatherCounts.same_weather_reapply_unexecuted,
       weather_executed_lifecycle_cases:weatherCounts.executed_lifecycle_cases,
       weather_scope:weatherCounts.scope,
       weather_diagnostic:"champions_weather_speed_probe.cjs"
     }:{}),
     ...(f.feature==="damage RNG and critical"?{
       damage_rounding_executed_synthetic_turns:roundingCounts.synthetic_executed_turns,
       damage_rounding_source_discriminating_rolls:roundingCounts.source_discriminators,
       damage_rounding_game_verified_turns:roundingCounts.game_verified_turns,
       damage_rounding_scope:roundingCounts.comparison_scope,
       damage_rounding_diagnostic:"champions_damage_rounding_executed_probe.cjs"
     }:{}),
     actual_game_status:"NOT_EVALUATED_PRIVATE_GOLDEN",
     feature_compatibility:"UNKNOWN",risk:f.risk,next_evidence:f.next_evidence,
     allowed_as_certain_ko_or_safe_switch_proof:false};
 });
 return {schema:"phase0-gate-a-evidence-inventory-v1",pinned_sha:PIN,format:FORMAT,
   scope:"OFFLINE_SOURCE_DIAGNOSTIC_ONLY",regulation:"M-C",
   gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",adoption:"NO_GO",
   feature_count:features.length,master_discrepancy_count:a.mismatch_count,
   wiki_comparable_count:w.result_count,wiki_mismatch_count:w.mismatch_count,
   modifier_source_status:"REVIEWED_SECONDARY_SOURCE_DIVERGENCE",
   known_modifier_source_conflicts:modifierCounts.conflicts,
   known_modifier_roll_discrepancies:modifierCounts.rollDifferences,
   modifier_comparison_scope:"SECONDARY_SOURCE_VS_PINNED_SHOWDOWN_NOT_DEVICE",
   modifier_diagnostic:"champions_damage_modifier_probe.cjs",
   turn_order_source_matches:turnCounts.matches,
   turn_order_inconclusive_ties:turnCounts.ties,
   turn_order_scope:"PINNED_SYNTHETIC_EXECUTED_TURNS_NOT_DEVICE",
   turn_order_diagnostic:"champions_turn_order_probe.cjs",
   paralysis_source_order_matches:paralysisCounts.matches,
   paralysis_unknown_compound_rounding:paralysisCounts.rounding_unknown,
   paralysis_scope:paralysisCounts.scope,
   paralysis_pinned_rate:"1/8_SOURCE_ONLY_NOT_DEVICE",
   paralysis_diagnostic:"champions_paralysis_probe.cjs",
   weather_source_order_matches:weatherCounts.matches,
   weather_unknown_compound_rounding:weatherCounts.rounding_unknown,
   weather_item_extension_source_agreement_unexecuted:weatherCounts.source_agreed_unexecuted,
       weather_same_kind_reapply_unexecuted:weatherCounts.same_weather_reapply_unexecuted,
   weather_executed_lifecycle_cases:weatherCounts.executed_lifecycle_cases,
   weather_scope:weatherCounts.scope,
   weather_diagnostic:"champions_weather_speed_probe.cjs",
   damage_rounding_executed_synthetic_turns:roundingCounts.synthetic_executed_turns,
   damage_rounding_source_discriminating_rolls:roundingCounts.source_discriminators,
   damage_rounding_game_verified_turns:roundingCounts.game_verified_turns,
   damage_rounding_scope:roundingCounts.comparison_scope,
   damage_rounding_diagnostic:"champions_damage_rounding_executed_probe.cjs",
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
module.exports={run,summarize,checkMap,checkEvidence,checkModifierEvidence,
 checkTurnOrderEvidence,checkParalysisEvidence,checkWeatherEvidence,checkExecutedRoundingEvidence,requirements,KNOWN_AUDIT_GROUPS,MAP};
