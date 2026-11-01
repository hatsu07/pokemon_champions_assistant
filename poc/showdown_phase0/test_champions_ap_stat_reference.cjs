"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {calculate,validatePublished,STATS,STATUS}=require("./champions_ap_stat_reference.cjs");
const {run,assess,masterStats,AP_POINTS,AP_TARGETS,NATURES,SPECIES}=
 require("./champions_ap_stat_probe.cjs");
const zero=()=>Object.fromEntries(STATS.map(k=>[k,0]));
const garchomp={hp:108,atk:130,def:95,spa:80,spd:85,spe:102};
test("independent Champions AP: 14 published speed examples, no Showdown input",()=>{
 assert.equal(validatePublished(),14);
 const r=calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),spe:32},nature:"Jolly"});
 assert.equal(r.status,STATUS);
 assert.equal(r.stats.hp,183);
 assert.equal(r.stats.spe,169);
 assert.equal(r.stats.spa,90); // floor((80+20) * 0.9)
 assert.equal(r.game_observed_level,false);
 assert.deepEqual(r.supported_scope,{species:"Garchomp",form:"base"});
});
test("HP and five non-HP stats respond to AP independent of nature",()=>{
 for(const nature of ["Serious","Jolly","Adamant","Brave"]){
   for(const stat of STATS){
     const none=calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:zero(),nature}).stats;
     const plus=calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),[stat]:32},nature}).stats;
     if(stat==="hp")assert.equal(plus.hp-none.hp,32);
     for(const other of STATS.filter(k=>k!==stat))assert.equal(plus[other],none[other]);
   }
 }
 assert.equal(calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:zero(),nature:"Serious"}).stats.hp,183);
});
test("nature rounding boundaries are floor and stat-specific",()=>{
 const s={hp:108,atk:130,def:95,spa:80,spd:85,spe:102};
 assert.equal(calculate({species:"Garchomp",form:"base",baseStats:s,ap:{...zero(),spe:8},nature:"Jolly"}).stats.spe,143);
 assert.equal(calculate({species:"Garchomp",form:"base",baseStats:s,ap:{...zero(),spe:7},nature:"Jolly"}).stats.spe,141);
 assert.equal(calculate({species:"Garchomp",form:"base",baseStats:s,ap:zero(),nature:"Brave"}).stats.spe,109);
 assert.equal(calculate({species:"Garchomp",form:"base",baseStats:s,ap:zero(),nature:"Brave"}).stats.atk,165);
});
test("unverified values, invalid AP and extra data are rejected",()=>{
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),spe:33},nature:"Jolly"}),/invalid Champions AP/);
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),hp:32,atk:32,spe:3},nature:"Jolly"}),/over 66/);
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),spe:null},nature:"Jolly"}),/invalid Champions AP/);
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:{...zero(),extra:0},nature:"Serious"}),/unknown or incomplete/);
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:zero(),nature:"UnverifiedNature"}),/unreviewed/);
 assert.throws(()=>calculate({species:"Garchomp",form:"base",baseStats:{...garchomp,hp:0},ap:zero(),nature:"Serious"}),/invalid Champions species base/);
});
test("two directly registrable Champions species use fixed master stats",()=>{
 assert.deepEqual(masterStats("ガブリアス"),garchomp);
 assert.deepEqual(masterStats("メタグロス"),{hp:80,atk:135,def:130,spa:95,spd:90,spe:70});
 assert.throws(()=>masterStats("UnreviewedForm"),/not unique/);
});
test("source-to-Showdown comparison rejects missing stat and reports mismatch without game claim",()=>{
 const expected=calculate({species:"Garchomp",form:"base",baseStats:garchomp,ap:zero(),nature:"Serious"}).stats;
 assert.equal(assess(expected,{...expected}).status,"SOURCE_SPEC_MATCH");
 const altered={...expected,spe:expected.spe+1};
 const flagged=assess(expected,altered);
 assert.equal(flagged.status,"SOURCE_SPEC_MISMATCH");
 assert.deepEqual(flagged.differences,[{stat:"spe",reference:expected.spe,pinned_showdown:altered.spe}]);
 assert.throws(()=>assess(expected,{...expected,hp:null}),/unreadable pinned/);
});
test("actual pinned Champions battle stats compare on legal registered six and selected three",()=>{
 const report=run();
 assert.equal(report.schema,"phase0-champions-ap-stat-source-comparison-v1");
 assert.deepEqual([report.gate_A,report.gate_B,report.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(report.official_or_game_confirmed,false);
 assert.equal(report.private_golden_read,false);
 assert.equal(report.game_case_count,0);
 assert.equal(report.live_use_approved,false);
 assert.equal(report.case_count,SPECIES.length*NATURES.length*AP_TARGETS.length*AP_POINTS.length);
 assert.equal(report.case_count,384);
 assert.equal(report.individual_stat_comparisons,2304);
 assert.equal(report.varied_ap_stat_count,6);
 assert.deepEqual(report.ap_targets,STATS);
 assert.deepEqual(report.varied_ap_case_counts,
   Object.fromEntries(STATS.map(k=>[k,64])));
 assert.equal(report.registration_ap_zero_fallback_cases,48);
 assert.equal(report.selected_form_scope,"TWO_BASE_SPECIES_ONLY");
 assert.equal(report.published_speed_reference_checks,14);
 assert.equal(report.counts.SOURCE_SPEC_MISMATCH,0);
 assert.equal(report.counts.SOURCE_SPEC_MATCH,192);
 assert.equal(report.registered_six_member_teams,true);
 assert.equal(report.selected_three_member_teams,true);
 assert.ok(report.unverified.length>0);
});

test("synthetic AP zero target remains legal by unrelated SpD allocation",()=>{
 const {run}=require("./champions_ap_stat_probe.cjs");
 // No device evidence: registration-specific edge case in the pinned simulator.
 const report=run();
 assert.equal(report.counts.SOURCE_SPEC_MISMATCH,0);
 assert.equal(report.case_count,384);
 assert.equal(report.registration_ap_zero_fallback_cases,48);
 assert.ok(report.sample_case_ids.some(s=>s.endsWith("/0")));
 assert.ok(report.ap_targets.includes("spd"));
});

test("unreviewed or missing species/form fails closed before any stat is calculated",()=>{
 const input={species:"Garchomp",form:"base",baseStats:garchomp,ap:zero(),nature:"Serious"};
 for(const change of [
   {species:"Shedinja",form:"base"},
   {species:"Garchomp",form:"mega"},
   {species:"Garchomp-Mega",form:"base"},
   {species:"Metagross",form:"mega"},
   {species:"Unknown",form:"base"},
   {species:null,form:"base"}
 ]){
   assert.throws(()=>calculate({...input,...change}),
     /UNSUPPORTED_CHAMPIONS_SPECIES_FORM_SCOPE/);
 }
 for(const field of ["species","form"]){
   const malformed={...input};delete malformed[field];
   assert.throws(()=>calculate(malformed),/unknown or incomplete Champions AP scope/);
 }
 assert.throws(()=>calculate({...input,internalLevel:50}),/unknown or incomplete Champions AP scope/);
 assert.deepEqual(calculate({...input,species:"Metagross"}).supported_scope,
   {species:"Metagross",form:"base"});
});
test("reference stat arithmetic recognizes AP changes in all six independently",()=>{
 for(const stat of STATS){
   const low=calculate({species:"Garchomp",form:"base",baseStats:garchomp,
     ap:zero(),nature:"Jolly"}).stats;
   const high=calculate({species:"Garchomp",form:"base",baseStats:garchomp,
     ap:{...zero(),[stat]:32},nature:"Jolly"}).stats;
   assert.notEqual(low[stat],high[stat],"AP change for "+stat);
   assert.equal(Object.keys(low).length,6);
 }
});
