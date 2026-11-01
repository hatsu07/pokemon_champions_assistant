"use strict";
// Phase 0 independent Champions-source ordering arithmetic. NEVER import Showdown.
const LEDGER=require("./champions_turn_order_source_ledger.json");
const {calculate}=require("./champions_ap_stat_reference.cjs");
const {masterStats}=require("./champions_ap_stat_probe.cjs");
const REVIEWED_SOURCES=Object.freeze({
 speed:"https://pkmn-cp-dataportal.com/battle/priority/speed-stats/",
 scarf:"https://pkmn-cp-dataportal.com/item/choice-items/",
 bullet:"https://pkmn-cp-dataportal.com/waza/%E3%83%90%E3%83%AC%E3%83%83%E3%83%88%E3%83%91%E3%83%B3%E3%83%81/",
 moves:"https://pkmn-cp-dataportal.com/move/",
 trickroom:"https://pkmn-cp-dataportal.com/battle/priority/trick-room/"
});
const REVIEWED_CASES=Object.freeze([
 ["normal-speed",2,2,false,0,false,"Swords Dance","Iron Defense","p1"],
 ["scarf-reverses",2,2,true,0,false,"Swords Dance","Iron Defense","p2"],
 ["plus-one-reverses",2,2,false,1,false,"Swords Dance","Iron Defense","p2"],
 ["priority-beats-speed",2,2,false,0,false,"Swords Dance","Bullet Punch","p2"],
 ["protect-above-bullet",2,2,false,0,false,"Protect","Bullet Punch","p1"],
 ["scarf-cannot-beat-priority",2,2,true,0,false,"Protect","Bullet Punch","p1"],
 ["speed-tie-unresolved",0,32,false,0,false,"Swords Dance","Iron Defense","UNRESOLVED_TIE"],
 ["trickroom-reverses",2,2,false,0,true,"Swords Dance","Iron Defense","p2"],
 ["trickroom-keeps-priority",2,2,false,0,true,"Protect","Bullet Punch","p1"],
 ["scarf-under-trickroom",2,2,true,0,true,"Swords Dance","Iron Defense","p1"]
]);
const REVIEWED_MOVES=Object.freeze([
 ["Garchomp","Swords Dance",0],["Garchomp","Protect",4],
 ["Garchomp","Earthquake",0],["Metagross","Iron Defense",0],
 ["Metagross","Bullet Punch",1]
]);
function exact(o,keys,label){
 if(!o||typeof o!=="object"||Array.isArray(o)||
    Object.keys(o).length!==keys.length||keys.some(k=>!Object.hasOwn(o,k)))
   throw Error("unreviewed "+label+" fields");
}
function validateLedger(l=LEDGER){
 if(l.schema!=="phase0-champions-turn-order-source-ledger-v1"||
    l.regulation!=="M-C"||l.checked_at!=="2026-11-01"||
    l.evidence!=="CHAMPIONS_SPECIFIC_SECONDARY_SOURCE_AND_FIXED_MASTER_NOT_DEVICE"||
    l.scope!=="TWO_BASE_SPECIES_ONLY_SYNTHETIC_SELECTED_TURNS"||
    l.actual_game_order_verified!==0||l.private_golden_read!==false||
    l.live_use_approved!==false||
    JSON.stringify(l.sources?.map(s=>[s.id,s.url]))!==
      JSON.stringify(Object.entries(REVIEWED_SOURCES))||
    JSON.stringify(l.reviewed_moves?.map(m=>[m.species,m.name,m.priority]))!==
      JSON.stringify(REVIEWED_MOVES)||
    JSON.stringify(l.cases?.map(c=>[
      c.id,c.g_ap,c.m_ap,c.m_scarf,c.m_stage,c.trick_room,c.p1,c.p2,c.expected
    ]))!==JSON.stringify(REVIEWED_CASES)||
    !Array.isArray(l.not_verified)||l.not_verified.length<5)
    throw Error("unreviewed Champions turn-order source contract drift");
 return true;
}
const MOVES=new Map(REVIEWED_MOVES.map(([species,name,priority])=>[species+"/"+name,priority]));
function baseSpeed(species,ap){
 if(!["Garchomp","Metagross"].includes(species)||
    !Number.isInteger(ap)||ap<0||ap>32)
   throw Error("UNSUPPORTED_CHAMPIONS_ORDER_SPECIES_AP");
 const points={hp:0,atk:0,def:0,spa:0,spd:0,spe:ap};
 const baseStats=masterStats(species==="Garchomp"?"ガブリアス":"メタグロス");
 return calculate({species,form:"base",baseStats,ap:points,nature:"Serious"}).stats.spe;
}
function effectiveSpeed(state){
 exact(state,["baseSpeed","stage","scarf","priority"],"speed state");
 if(!Number.isInteger(state.baseSpeed)||state.baseSpeed<1||
    !Number.isInteger(state.priority)||state.priority< -7||state.priority>7||
    ![0,1].includes(state.stage)||typeof state.scarf!=="boolean")
    throw Error("UNSUPPORTED_CHAMPIONS_ORDER_STATE");
 if(state.stage===1&&state.scarf)
    throw Error("UNSUPPORTED_COMBINED_SPEED_MODIFIER_ROUNDING");
 let speed=state.baseSpeed;
 if(state.stage===1)speed=Math.floor(speed*3/2);
 if(state.scarf)speed=Math.floor(speed*3/2);
 return speed;
}
function compareOrder(p1,p2,trickRoom){
 if(typeof trickRoom!=="boolean")throw Error("unreviewed Trick Room state");
 const a=effectiveSpeed(p1),b=effectiveSpeed(p2);
 const first=p1.priority!==p2.priority?
   (p1.priority>p2.priority?"p1":"p2"):
   a===b?"UNRESOLVED_TIE":
   (trickRoom?(a<b?"p1":"p2"):(a>b?"p1":"p2"));
 return {status:first==="UNRESOLVED_TIE"?"UNKNOWN_TIE":"SOURCE_ARITHMETIC_ONLY",
   expected_first:first,p1_effective_speed:a,p2_effective_speed:b,
   source_evidence:"CHAMPIONS_SECONDARY_SOURCE_NOT_DEVICE"};
}
function referenceCase(c){
 validateLedger();
 if(!LEDGER.cases.includes(c))throw Error("unreviewed case identity");
 const p1priority=MOVES.get("Garchomp/"+c.p1),
       p2priority=MOVES.get("Metagross/"+c.p2);
 if(p1priority===undefined||p2priority===undefined)
   throw Error("unreviewed legal move priority");
 const g=baseSpeed("Garchomp",c.g_ap),m=baseSpeed("Metagross",c.m_ap);
 const order=compareOrder(
   {baseSpeed:g,stage:0,scarf:false,priority:p1priority},
   {baseSpeed:m,stage:c.m_stage,scarf:c.m_scarf,priority:p2priority},
   c.trick_room);
 if(order.expected_first!==c.expected)throw Error("reviewed expected order drift: "+c.id);
 return {id:c.id,p1_base_speed:g,p2_base_speed:m,
   p1_priority:p1priority,p2_priority:p2priority,...order};
}
module.exports={LEDGER,REVIEWED_SOURCES,REVIEWED_CASES,REVIEWED_MOVES,
 validateLedger,baseSpeed,effectiveSpeed,compareOrder,referenceCase};
