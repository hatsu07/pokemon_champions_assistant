"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {LEDGER,checkLedger,secondaryComparison}=require("./champions_damage_rounding_secondary_reference.cjs");
const {run}=require("./champions_damage_rounding_executed_probe.cjs");
test("two independent secondary text claims and their non-device scope are immutable",()=>{
 assert.equal(checkLedger(),true);
 assert.equal(LEDGER.source_claims.length,2);
 assert.equal(LEDGER.source_claims[1].confidence,
   "SECONDARY_DESCRIPTION_ONLY_NOT_FULL_FORMULA_NUMERIC_MATCH");
 for(const mutate of [
   x=>{x.source_claims[0].url="https://unreviewed.example";},
   x=>{x.source_claims[1].claim="game verified";},
   x=>{x.source_claims[1].confidence="DEVICE_VERIFIED";},
   x=>{x.specific_scope.critical=true;},
   x=>{x.specific_scope.witness_rolls[0]=85;},
   x=>{x.specific_scope.combined_expected[0]=21;},
   x=>{x.specific_scope.sequential_expected[0]=22;},
   x=>{x.classification="DEVICE_PARITY";},
   x=>{x.unverified[0]="Device damage proven";},
   x=>{x.device_observations=16;},
   x=>{x.private_golden_read=true;},
   x=>{x.live_use_approved=true;}
 ]){
   const l=structuredClone(LEDGER);mutate(l);
   assert.throws(()=>checkLedger(l),/secondary source contract drift/);
 }
});
test("quarter once vs floor of successive halves differ exactly at reviewed four rolls",()=>{
 const r=secondaryComparison();
 assert.equal(r.row_count,16);
 assert.equal(r.witness_count,4);
 assert.equal(r.game_verified,false);
 assert.deepEqual(r.witnesses.map(x=>x.roll_pct),[86,90,91,98]);
 assert.deepEqual(r.witnesses.map(x=>x.pre_final),[87,91,91,99]);
 assert.deepEqual(r.witnesses.map(x=>x.combined),[22,23,23,25]);
 assert.deepEqual(r.witnesses.map(x=>x.sequential),[21,22,22,24]);
 assert.equal(r.rows.filter(x=>x.discriminating).length,4);
});
test("actual pinned M-C moves show HP losses matching separately rounded source step",()=>{
 const r=run();
 assert.equal(r.schema,"phase0-champions-damage-rounding-executed-v1");
 assert.equal(r.pinned_sha,"ccfaa20f32af363569cd21a9d501b2bc1ded26b1");
 assert.equal(r.format,"gen9championsbssregmc");
 assert.deepEqual([r.gate_A,r.gate_B,r.adoption],["UNKNOWN","UNSUPPORTED","NO_GO"]);
 assert.equal(r.game_observed_turns,0);
 assert.equal(r.private_golden_read,false);
 assert.equal(r.live_use_approved,false);
 assert.equal(r.roll_count,16);
 assert.equal(r.executed_turn_count,16);
 assert.equal(r.discriminating_witness_count,4);
 assert.deepEqual(r.results.map(x=>x.roll_pct),Array.from({length:16},(_,i)=>85+i));
 const witness=r.results.filter(x=>x.status==="EXECUTED_MATCHES_SEQUENTIAL_DIFFERS_COMBINED");
 assert.deepEqual(witness.map(x=>x.roll_pct),[86,90,91,98]);
 for(const row of r.results){
   assert.equal(row.actual_hp_delta,row.pinned_direct_damage);
   assert.equal(row.actual_hp_delta,row.secondary_sequential);
   assert.deepEqual(row.observed_move_order,["p1","p2"]);
   assert.deepEqual(row.observed_moves,{
     p1:{id:"earthquake",name:"Earthquake",priority:0},
     p2:{id:"surf",name:"Surf",priority:0}
   });
   assert.equal(row.event_trace.length,3);
   assert.match(row.event_trace[0],/^\|move\|p1a: Garchomp\|Earthquake\|/);
   assert.match(row.event_trace[1],/^\|-damage\|p2a: Blastoise\|/);
   assert.match(row.event_trace[2],/^\|move\|p2a: Blastoise\|Surf\|/);
   assert.ok(row.seed>=1&&row.seed<=48);
   assert.equal(row.provenance,"PINNED_SYNTHETIC_EXECUTED_TURN_BURN_REFLECT_NOT_DEVICE");
 }
});
