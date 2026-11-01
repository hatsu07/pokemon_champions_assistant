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
 const r=calculate({baseStats:garchomp,ap:{...zero(),spe:32},nature:"Jolly"});
 assert.equal(r.status,STATUS);
 assert.equal(r.stats.hp,183);
 assert.equal(r.stats.spe,169);
 assert.equal(r.stats.spa,90); // floor((80+20) * 0.9)
 assert.equal(r.game_observed_level,false);
});
test("HP and five non-HP stats respond to AP independent of nature",()=>{
 for(const nature of ["Serious","Jolly","Adamant","Brave"]){
   for(const stat of STATS){
     const none=calculate({baseStats:garchomp,ap:zero(),nature}).stats;
     const plus=calculate({baseStats:garchomp,ap:{...zero(),[stat]:32},nature}).stats;
     if(stat==="hp")assert.equal(plus.hp-none.hp,32);
     for(const other of STATS.filter(k=>k!==stat))assert.equal(plus[other],none[other]);
   }
 }
 assert.equal(calculate({baseStats:garchomp,ap:zero(),nature:"Serious"}).stats.hp,183);
});
test("nature rounding boundaries are floor and stat-specific",()=>{
 const s={hp:108,atk:130,def:95,spa:80,spd:85,spe:102};
 assert.equal(calculate({baseStats:s,ap:{...zero(),spe:8},nature:"Jolly"}).stats.spe,143);
 assert.equal(calculate({baseStats:s,ap:{...zero(),spe:7},nature:"Jolly"}).stats.spe,141);
 assert.equal(calculate({baseStats:s,ap:zero(),nature:"Brave"}).stats.spe,109);
 assert.equal(calculate({baseStats:s,ap:zero(),nature:"Brave"}).stats.atk,165);
});
test("unverified values, invalid AP and extra data are rejected",()=>{
 assert.throws(()=>calculate({baseStats:garchomp,ap:{...zero(),spe:33},nature:"Jolly"}),/invalid Champions AP/);
 assert.throws(()=>calculate({baseStats:garchomp,ap:{...zero(),hp:32,atk:32,spe:3},nature:"Jolly"}),/over 66/);
 assert.throws(()=>calculate({baseStats:garchomp,ap:{...zero(),spe:null},nature:"Jolly"}),/invalid Champions AP/);
 assert.throws(()=>calculate({baseStats:garchomp,ap:{...zero(),extra:0},nature:"Serious"}),/unknown or incomplete/);
 assert.throws(()=>calculate({baseStats:garchomp,ap:zero(),nature:"UnverifiedNature"}),/unreviewed/);
 assert.throws(()=>calculate({baseStats:{...garchomp,hp:0},ap:zero(),nature:"Serious"}),/invalid Champions species base/);
});
test("two directly registrable Champions species use fixed master stats",()=>{
 assert.deepEqual(masterStats("ガブリアス"),garchomp);
 assert.deepEqual(masterStats("メタグロス"),{hp:80,atk:135,def:130,spa:95,spd:90,spe:70});
 assert.throws(()=>masterStats("UnreviewedForm"),/not unique/);
});
test("source-to-Showdown comparison rejects missing stat and reports mismatch without game claim",()=>{
 const expected=calculate({baseStats:garchomp,ap:zero(),nature:"Serious"}).stats;
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
 assert.equal(report.case_count,192);
 assert.equal(report.individual_stat_comparisons,1152);
 assert.equal(report.published_speed_reference_checks,14);
 assert.equal(report.counts.SOURCE_SPEC_MISMATCH,0);
 assert.equal(report.counts.SOURCE_SPEC_MATCH,192);
 assert.equal(report.registered_six_member_teams,true);
 assert.equal(report.selected_three_member_teams,true);
 assert.ok(report.unverified.length>0);
});
