"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {run,masterMega,projection,checkMega,partialStatus,REQUIRED_OBSERVABLES,
 UNOBSERVABLE_INTERNALS}=require("./post_mega_restoration_probe.cjs");
const {runB1,restoreChecked}=require("./gate_b_probe.cjs");
const {fingerprint,normalizeReplayTimestamps}=require("./state_compare.cjs");
const case10=require("./gate_b_cases.json").cases.find(c=>c.id==="B1-10");
let report,originalCase;
test("Mega Lucario identity, types, ability, base stats come from Champions fixed master",()=>{
 const m=masterMega();
 assert.equal(m.species,"lucariomega");
 assert.deepEqual(m.types,["Fighting","Steel"]);
 assert.equal(m.ability,"adaptability");
 assert.equal(m.ability_ja,"てきおうりょく");
 assert.equal(m.baseStats.spe,112);
});
test("executed Mega turn is preserved in a complete trusted snapshot and both branches",()=>{
 report=run();
 assert.equal(report.schema,"phase0-post-mega-restoration-audit-v1");
 assert.equal(report.pinned_sha,"ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
 assert.equal(report.format,"gen9championsbssregmc");
 assert.deepEqual([report.gate_A,report.gate_B,report.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(report.full_synthetic_snapshot_status,"PASS_SYNTHETIC");
 assert.equal(report.original_gate_b_case,"B1-10");
 assert.equal(report.game_verified_cases,0);
 assert.equal(report.actual_game_reconstruction,false);
 assert.equal(report.private_golden_read,false);
 assert.equal(report.live_use_approved,false);
 assert.equal(report.branch_replay_equal,true);
 assert.equal(report.alternative_branch_distinct,true);
 assert.equal(report.source_unchanged,true);
 assert.deepEqual(report.before,report.after);
 assert.equal(report.before.species,"lucariomega");
 assert.equal(report.before.ability,"adaptability");
 assert.deepEqual(report.before.types,["Fighting","Steel"]);
 assert.equal(report.before.base_stats.spe,112);
 assert.equal(report.before.mega_events.length,1);
 assert.equal(report.before.selected[2],"lucariomega");
 assert.ok(report.before.hp>0&&report.before.hp<=report.before.maxhp);
 assert.ok(report.before.speed>0&&Number.isInteger(report.before.speed));
 assert.ok(report.before.moves.some(x=>x.pp<x.maxpp));
 assert.ok(report.checked_properties.includes("move_pp"));
 assert.ok(report.checked_properties.includes("mega_event_history"));
 assert.equal(report.partial_observation.status,"UNSUPPORTED");
 assert.equal(report.partial_observation.complete_snapshot_restorable_from_observation,false);
});
test("a partial Mega screen never inherits complete synthetic engine state",()=>{
 const incomplete=Object.fromEntries([...REQUIRED_OBSERVABLES,...UNOBSERVABLE_INTERNALS]
  .map(k=>[k,{source:"UNKNOWN"}]));
 const assessment=partialStatus(incomplete);
 assert.deepEqual(assessment.missing_observed_or_inferred_mega_fields,REQUIRED_OBSERVABLES);
 assert.deepEqual(assessment.internal_snapshot_not_authenticated,UNOBSERVABLE_INTERNALS);
 assert.equal(assessment.status,"UNSUPPORTED");
 const current={...incomplete,opponent_mega_state:{source:"GAME_OBSERVED",value:true},
  opponent_form:{source:"GAME_OBSERVED",value:"lucariomega"},
  opponent_current_type:{source:"GAME_OBSERVED",value:["Fighting","Steel"]}};
 const still=partialStatus(current);
 assert.equal(still.status,"UNSUPPORTED");
 assert.ok(still.missing_observed_or_inferred_mega_fields.includes("opponent_mega_history"));
 const all=Object.fromEntries([...REQUIRED_OBSERVABLES,...UNOBSERVABLE_INTERNALS]
  .map(k=>[k,{source:"GAME_OBSERVED",value:"not a native battle object"}]));
 assert.equal(partialStatus(all).status,"UNSUPPORTED");
 assert.equal(partialStatus(all).complete_snapshot_restorable_from_observation,false);
 assert.throws(()=>partialStatus(null),/partial mega facts required/);
});
test("Mega historical fields cannot be silently corrupted under original trusted digest",()=>{
 originalCase=runB1(case10);
 assert.equal(originalCase.status,"PASS_SYNTHETIC");
 const json=originalCase.restore_source_state,digest=originalCase.input_state_sha256;
 const controls=[
  ["mega form",x=>{x.sides[1].pokemon[2].species="lucario";}],
  ["mega ability",x=>{x.sides[1].pokemon[2].ability="innerfocus";}],
  ["mega HP",x=>{x.sides[1].pokemon[2].hp--; }],
  ["mega PP",x=>{x.sides[1].pokemon[2].moveSlots[0].pp--; }],
  ["mega history",x=>{x.log=x.log.filter(s=>!s.startsWith("|-mega|"));}],
  ["RNG",x=>{x.prng="1,2,3,4";}],
  ["queue",x=>{x.queue.push({choice:"move",moveid:"earthquake"});}]
 ];
 const trusted=restoreChecked(json,digest);
 assert.equal(checkMega(trusted).species,"lucariomega");
 assert.deepEqual(projection(trusted),report.before);
 for(const [label,change] of controls){
  const mutated=structuredClone(json);change(mutated);
  assert.notEqual(fingerprint(normalizeReplayTimestamps(mutated)),digest,label);
  assert.throws(()=>restoreChecked(mutated,digest),/snapshot input integrity mismatch/,label);
 }
 assert.equal(fingerprint(normalizeReplayTimestamps(json)),digest);
});
test("unreviewed Master trait or a forged Mega form never produces a PASS",()=>{
 const b=restoreChecked(originalCase.restore_source_state,originalCase.input_state_sha256);
 b.p2.active[0].ability="innerfocus";
 assert.throws(()=>checkMega(b),/disagrees with Champions master/);
 const b2=restoreChecked(originalCase.restore_source_state,originalCase.input_state_sha256);
 b2.p2.active[0].species=b2.dex.species.get("Lucario");
 assert.throws(()=>projection(b2),/actual selected active Mega absent/);
});
