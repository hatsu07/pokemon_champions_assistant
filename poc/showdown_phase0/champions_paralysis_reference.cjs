"use strict";
// Phase 0 independent Champions-secondary-source arithmetic. Do not import Showdown.
const LEDGER=require("./champions_paralysis_order_ledger.json");
const {baseSpeed}=require("./champions_turn_order_reference.cjs");
const SOURCE_URLS=Object.freeze([
 ["gamewith","https://gamewith.jp/pokemon-champions/559883"],
 ["serebii","https://www.serebii.net/pokemonchampions/statusconditions.shtml"],
 ["speed","https://pkmn-cp-dataportal.com/battle/priority/speed-stats/"],
 ["scarf","https://pkmn-cp-dataportal.com/item/choice-items/"],
 ["trickroom","https://pkmn-cp-dataportal.com/battle/priority/trick-room/"],
 ["paralysis","https://pkmn-cp-dataportal.com/status/paralysis/"]
]);
const REVIEWED_CASES=Object.freeze([
 ["metagross-par",false,true,false,false,0,false,"Swords Dance","Iron Defense",2,"p1"],
 ["garchomp-par",true,false,false,false,0,false,"Swords Dance","Iron Defense",2,"p2"],
 ["both-par",true,true,false,false,0,false,"Swords Dance","Iron Defense",2,"p1"],
 ["metagross-par-trick-room",false,true,false,false,0,true,"Swords Dance","Iron Defense",2,"p2"],
 ["metagross-par-priority",false,true,false,false,0,false,"Swords Dance","Bullet Punch",2,"p2"],
 ["garchomp-par-protect",true,false,false,false,0,false,"Protect","Bullet Punch",2,"p1"],
 ["garchomp-par-scarf",true,false,true,false,0,false,"Swords Dance","Iron Defense",2,"p1"],
 ["metagross-par-plus-one",false,true,false,false,1,false,"Swords Dance","Iron Defense",2,"p1"],
 ["metagross-par-scarf",false,true,false,true,0,false,"Swords Dance","Iron Defense",2,"p1"],
 ["triple-modifier-odd-unreviewed",false,true,false,true,1,false,"Swords Dance","Iron Defense",3,"UNREVIEWED_ROUNDING"]
]);
const MOVES=new Map([["Swords Dance",0],["Iron Defense",0],["Bullet Punch",1],["Protect",4]]);
const COLUMNS=["id","g_par","m_par","g_scarf","m_scarf","m_stage",
 "trick_room","p1","p2","m_ap","expected"];
function checkLedger(l=LEDGER){
 if(l.schema!=="phase0-champions-paralysis-order-ledger-v1"||
    l.regulation!=="M-C"||l.checked_at!=="2026-11-01"||
    l.evidence!=="CHAMPIONS_SECONDARY_SOURCE_PLUS_FIXED_MASTERS_NOT_DEVICE"||
    l.scope!=="TWO_BASE_SPECIES_SYNTHETIC_PRETURN_PARALYSIS"||
    l.game_verified_turn_count!==0||l.private_golden_read!==false||
    l.live_use_approved!==false||
    JSON.stringify(l.sources?.map(s=>[s.id,s.url]))!==JSON.stringify(SOURCE_URLS)||
    JSON.stringify(l.full_paralysis_rate)!==JSON.stringify({
      numerator:1,denominator:8,
      verification:"PUBLISHED_CHAMPIONS_SPEC_AND_PINNED_SOURCE_ONLY_NOT_EMPIRICAL"})||
    JSON.stringify(l.cases?.map(c=>COLUMNS.map(k=>c[k])))!==JSON.stringify(REVIEWED_CASES)||
    !Array.isArray(l.not_verified)||l.not_verified.length!==6)
   throw Error("Champions paralysis source contract drift");
 for(const c of l.cases)if(Object.keys(c).length!==12||c.g_ap!==2)
   throw Error("Champions paralysis case unexpected fields");
 return true;
}
function speeds(state){
 if(!state||Object.keys(state).sort().join(",")!=="base,par,scarf,stage"||
    !Number.isInteger(state.base)||state.base<1||
    typeof state.par!=="boolean"||typeof state.scarf!=="boolean"||
    ![0,1].includes(state.stage))
   throw Error("UNSUPPORTED_PARALYSIS_SPEED_STATE");
 const operations=[];
 if(state.stage===1)operations.push([3,2,"stage"]);
 if(state.scarf)operations.push([3,2,"scarf"]);
 if(state.par)operations.push([1,2,"par"]);
 const outputs=new Set();
 function visit(remaining,number){
   if(!remaining.length){outputs.add(number);return}
   for(const [i,op] of remaining.entries())
     visit(remaining.filter((_,j)=>j!==i),Math.max(1,Math.floor(number*op[0]/op[1])));
 }
 visit(operations,state.base);
 const possible=[...outputs].sort((a,b)=>a-b);
 return {status:possible.length===1?"ROUNDING_ORDER_INVARIANT":"UNVERIFIED_MODIFIER_ORDER",
   possible_speeds:possible,effective_speed:possible.length===1?possible[0]:null};
}
function reference(c){
 checkLedger();
 if(!LEDGER.cases.includes(c))throw Error("unreviewed paralysis case identity");
 const a=baseSpeed("Garchomp",c.g_ap),b=baseSpeed("Metagross",c.m_ap);
 const g=speeds({base:a,par:c.g_par,scarf:c.g_scarf,stage:0});
 const m=speeds({base:b,par:c.m_par,scarf:c.m_scarf,stage:c.m_stage});
 const p1Priority=MOVES.get(c.p1),p2Priority=MOVES.get(c.p2);
 if(p1Priority===undefined||p2Priority===undefined)
   throw Error("unreviewed move priority");
 const incomplete=g.effective_speed===null||m.effective_speed===null;
 const first=incomplete?"UNREVIEWED_ROUNDING":
   p1Priority!==p2Priority?(p1Priority>p2Priority?"p1":"p2"):
     g.effective_speed===m.effective_speed?"UNRESOLVED_TIE":
       c.trick_room?(g.effective_speed<m.effective_speed?"p1":"p2"):
       (g.effective_speed>m.effective_speed?"p1":"p2");
 if(first!==c.expected)throw Error("Champions source order drift "+c.id);
 return {id:c.id,expected_first:first,source_status:incomplete?"UNKNOWN_ROUNDING":
     first==="UNRESOLVED_TIE"?"INCONCLUSIVE_SPEED_TIE":"SOURCE_ARITHMETIC_ONLY",
   p1_base_speed:a,p2_base_speed:b,p1_effective_speed:g.effective_speed,
   p2_effective_speed:m.effective_speed,p1_possible_speeds:g.possible_speeds,
   p2_possible_speeds:m.possible_speeds,p1_priority:p1Priority,p2_priority:p2Priority};
}
module.exports={LEDGER,SOURCE_URLS,REVIEWED_CASES,checkLedger,speeds,reference};
