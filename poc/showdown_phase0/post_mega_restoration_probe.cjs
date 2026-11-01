"use strict";
// Gate B Phase 0 ONLY. Engine-origin complete synthetic mega snapshots, never device recovery.
const fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const {rows,types}=require("./audit.cjs");
const {runB1,restoreChecked,assertPinnedMasterSets,state,same,resolveFrom}=require("./gate_b_probe.cjs");
const {fingerprint,stableStringify}=require("./state_compare.cjs");
const {pinned}=require("./worker.cjs");
const MANIFEST=require("./gate_b_cases.json");
const FORMAT="gen9championsbssregmc";
const REQUIRED_OBSERVABLES=Object.freeze([
 "opponent_form","opponent_mega_state","opponent_mega_history",
 "opponent_current_type","opponent_ability","opponent_hp_exact",
 "opponent_remaining_pp","opponent_boosts","opponent_item"
]);
const UNOBSERVABLE_INTERNALS=Object.freeze([
 "internal_rng","unresolved_queue","unobserved_effect_history","unknown_opponent_set"
]);
function masterMega(){
 const folder=process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,"masters");
 const records=rows(fs.readFileSync(path.join(folder,"Pokemon_Champions_MC_usable_pokemon.csv"),"utf8"));
 const matched=records.filter(r=>r["全国No."]==="#0448"&&r["ポケモン名"]==="メガルカリオ"&&
   r["区分"]==="メガシンカ"&&r["フォーム/姿"]==="");
 if(matched.length!==1||matched[0]["特性"]?.trim()!=="てきおうりょく")
   throw Error("Champions master Mega Lucario identity or ability changed");
 const m=matched[0];
 const expectedTypes=[m["タイプ1"],m["タイプ2"]].filter(Boolean).map(t=>types[t]);
 const keys={hp:"H",atk:"A",def:"B",spa:"C",spd:"D",spe:"S"};
 const baseStats=Object.fromEntries(Object.entries(keys).map(([k,v])=>[k,Number(m[v])]));
 if(expectedTypes.length!==2||expectedTypes.some(x=>!x)||
    Object.values(baseStats).some(x=>!Number.isInteger(x)||x<=0))
   throw Error("Champions master Mega Lucario types/stats invalid");
 return {species:"lucariomega",types:expectedTypes,ability:"adaptability",
   ability_ja:m["特性"].trim(),baseStats,master_species:"メガルカリオ"};
}
function projection(b){
 const p=b.p2?.active?.[0];
 if(!p||!p.species?.isMega)throw Error("actual selected active Mega absent");
 const megaEvents=b.log.filter(x=>x.startsWith("|-mega|p2a: Lucario|"));
 const moves=p.moveSlots.map(m=>({id:m.id,pp:m.pp,maxpp:m.maxpp}));
 if(megaEvents.length!==1||b.requestState!=="move"||
    b.p1.pokemon.length!==3||b.p2.pokemon.length!==3||
    !moves.length||moves.some(m=>!m.id||!Number.isInteger(m.pp)||m.pp<0||
      !Number.isInteger(m.maxpp)||m.pp>m.maxpp))
   throw Error("post-Mega log/selection/moves precondition changed");
 return {species:p.species.id,types:[...p.types],ability:p.ability,
   base_stats:{...p.species.baseStats},speed:p.getStat("spe"),
   hp:p.hp,maxhp:p.maxhp,boosts:{...p.boosts},
   item:p.item,last_item:p.lastItem||"",moves,
   mega_events:[...megaEvents],selected:p.side.pokemon.map(x=>x.species.id),
   current_request:b.requestState};
}
function checkMega(b,master=masterMega()){
 const p=projection(b);
 const invalid=[];
 if(p.species!==master.species)invalid.push("form");
 if(stableStringify(p.types)!==stableStringify(master.types))invalid.push("types");
 if(p.ability!==master.ability)invalid.push("ability");
 if(stableStringify(p.base_stats)!==stableStringify(master.baseStats))invalid.push("base_stats");
 if(p.item!=="lucarionite")invalid.push("mega_stone");
 if(!Number.isInteger(p.speed)||p.speed<=0)invalid.push("speed");
 if(!Number.isInteger(p.hp)||p.hp<=0||p.hp>p.maxhp)invalid.push("hp");
 if(p.selected.length!==3||new Set(p.selected).size!==3||p.selected[0]!==master.species)invalid.push("selected");
 if(invalid.length)throw Error("pinned Mega form/type/ability/stats/speed/item/HP or selected position disagrees with Champions master: "+JSON.stringify({
   invalid,actual:{species:p.species,types:p.types,ability:p.ability,
     base_stats:p.base_stats,item:p.item,speed:p.speed,hp:p.hp,maxhp:p.maxhp,selected:p.selected},
   expected:master}));
 return p;
}
function partialStatus(facts){
 if(!facts||typeof facts!=="object"||Array.isArray(facts))throw Error("partial mega facts required");
 const missing=REQUIRED_OBSERVABLES.filter(key=>!Object.hasOwn(facts,key)||
   !facts[key]||facts[key].source==="UNKNOWN"||facts[key].source==="INFERRED");
 const internal=UNOBSERVABLE_INTERNALS.filter(key=>!Object.hasOwn(facts,key)||
   !facts[key]||facts[key].source==="UNKNOWN"||facts[key].source==="INFERRED");
 // Even a fully populated public display is not a trusted internal state snapshot.
 return {status:"UNSUPPORTED",complete_snapshot_restorable_from_observation:false,
   missing_observed_or_inferred_mega_fields:missing,
   internal_snapshot_not_authenticated:internal,
   reason:"partial observation lacks independently trusted full Battle history and RNG; no automatic reconstruct"};
}
function run(){
 const sha=assertPinnedMasterSets();
 if(sha!==pinned)throw Error("pinned M-C source changed");
 const entry=MANIFEST.cases.find(c=>c.id==="B1-10");
 if(!entry||entry.setup!=="mega"||entry.mechanism!=="post_mega_history"||
    entry.expected_status!=="PASS_SYNTHETIC")
   throw Error("existing Gate B engine Mega scenario absent");
 const r=runB1(entry);
 if(r.status!=="PASS_SYNTHETIC"||r.setup_provenance!=="SHOWDOWN_EXECUTED_TURN"||
    !r.branch_independence?.repeat_reproduced||
    !r.branch_independence?.source_unchanged||
    !r.branch_independence?.restored_unchanged||
    !r.branch_independence?.distinct_results||
    r.first_difference_field!==null)
   throw Error("existing B1 Mega case not reproducibly restored and branched");
 const master=masterMega();
 const original=restoreChecked(r.restore_source_state,r.input_state_sha256);
 const restored=restoreChecked(r.restored_state,r.input_state_sha256);
 const before=checkMega(original,master),after=checkMega(restored,master);
 if(!same(original,restored)||stableStringify(before)!==stableStringify(after))
   throw Error("post-Mega properties changed after complete synthetic restore");
 const sourceDigest=fingerprint(state(original)),restoredDigest=fingerprint(state(restored));
 const branch=resolveFrom(restored,"move 1");
 const replay=resolveFrom(restored,"move 1");
 const alternate=resolveFrom(restored,"switch 2");
 if(!same(branch,replay)||same(branch,alternate)||
    fingerprint(state(original))!==sourceDigest||
    fingerprint(state(restored))!==restoredDigest)
   throw Error("post-Mega replay diverged or independent branches mutated source");
 const unknownFacts=Object.fromEntries([...REQUIRED_OBSERVABLES,...UNOBSERVABLE_INTERNALS]
   .map(key=>[key,{source:"UNKNOWN"}]));
 const partial=partialStatus(unknownFacts);
 if(partial.status!=="UNSUPPORTED"||
    partial.missing_observed_or_inferred_mega_fields.length!==REQUIRED_OBSERVABLES.length||
    partial.internal_snapshot_not_authenticated.length!==UNOBSERVABLE_INTERNALS.length)
   throw Error("unknown Mega observation incorrectly promoted");
 return {schema:"phase0-post-mega-restoration-audit-v1",format:FORMAT,pinned_sha:sha,
   regulation:"M-C",evidence:"COMPLETE_ENGINE_SYNTHETIC_SNAPSHOT_ONLY",
   original_gate_b_case:"B1-10",game_verified_cases:0,
   actual_game_reconstruction:false,private_golden_read:false,
   trusted_digest_scope:"SAME_SYNTHETIC_COMPLETE_BATTLE_ORIGIN_NOT_PROOF_OF_DEVICE",
   full_synthetic_snapshot_status:"PASS_SYNTHETIC",
   checked_properties:["form","types","ability","base_stats","effective_speed",
     "hp","maxhp","boosts","item","last_item","move_pp","mega_event_history",
     "selected_three","turn_request"],
   master_species:master.master_species,master_ability:master.ability_ja,
   before,after,snapshot_sha256:sourceDigest,
   branch_replay_equal:true,alternative_branch_distinct:true,source_unchanged:true,
   partial_observation:partial,gate_A:"UNKNOWN",gate_B:"UNSUPPORTED",
   adoption:"NO_GO",live_use_approved:false};
}
if(require.main===module){
 try{process.stdout.write(JSON.stringify(run())+"\n")}
 catch(e){process.stdout.write(JSON.stringify({status:"NOT_RUN",reason:e.message})+"\n");process.exitCode=2}
}
module.exports={run,masterMega,projection,checkMega,partialStatus,REQUIRED_OBSERVABLES,UNOBSERVABLE_INTERNALS};
