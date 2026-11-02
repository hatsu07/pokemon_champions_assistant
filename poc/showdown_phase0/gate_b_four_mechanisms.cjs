"use strict";
// Phase-0 ONLY. All accepted states are conditional synthetic hypotheses.
// Evidence metadata is asserted by the caller; a GAME_FRAME label is not device proof.
const {moveKey}=require("./champions_move_key.cjs");
const observed=f=>["SYNTHETIC","GAME_OBSERVED"].includes(f?.source);
const exact=(o,keys)=>o && typeof o==="object" && !Array.isArray(o) &&
 Object.keys(o).sort().join(",")===keys.slice().sort().join(",");
const limit=(n,max=8)=>Number.isInteger(n)&&n>=1&&n<=max;
function inspect(obs,ctx){
 const f=obs.facts;
 const has=k=>observed(f[k]);
 const deny=reason=>({error:reason});
 const frameFor=(keys)=>{
   const refs=f.request_state.evidence_ids;
   if(!has("request_state")||refs?.length!==1)return null;
   const frame=obs.evidence.find(e=>e.id===refs[0]);
   if(!frame||typeof frame.match_id!=="string"||!frame.match_id||
     !Number.isInteger(frame.turn_epoch)||frame.turn_epoch<0||
     !Number.isInteger(frame.state_revision)||frame.state_revision<0||
     !keys.every(k=>has(k)&&f[k].evidence_ids?.includes(frame.id)))return null;
   return frame;
 };
 const historical=(key,keys,frame)=>{
   const fact=f[key],event=fact?.value;
   if(!has(key)||!exact(event,keys)||fact.evidence_ids?.length!==1)return false;
   const evidence=obs.evidence.find(e=>e.id===fact.evidence_ids[0]);
   return !!(evidence&&typeof evidence.match_id==="string"&&
     typeof frame.active_instance_id==="string"&&frame.active_instance_id.length>0&&
     evidence.active_instance_id===frame.active_instance_id&&
     evidence.match_id===frame.match_id&&
     evidence.turn_epoch===event.turn_epoch&&
     evidence.state_revision===event.state_revision&&
     Number.isInteger(event.turn_epoch)&&event.turn_epoch>=0&&
     Number.isInteger(event.state_revision)&&event.state_revision>=0&&
     (event.turn_epoch<frame.turn_epoch||
      event.turn_epoch===frame.turn_epoch&&event.state_revision<=frame.state_revision));
 };
 const choice=has("choice_lock")?f.choice_lock.value:null;
 const removed=has("opponent_removed_item")?f.opponent_removed_item.value:null;
 const mega=has("opponent_mega_history")?f.opponent_mega_history.value:null;
 const weather=f.weather.value,terrain=f.terrain.value,rawHazards=f.hazards.value;
 // An old list alone cannot prove which side contains the hazard.
 const hazards=Array.isArray(rawHazards)?rawHazards:
   exact(rawHazards,["side","effects","turn_epoch","state_revision"])&&
   rawHazards.side==="p2"&&Array.isArray(rawHazards.effects)?rawHazards.effects:null;
 if(has("action_history"))return deny("other action history cannot be reconstructed");
 if(has("pending_choice")||has("unresolved_queue"))
   return deny("unresolved action or pending choice is not reconstructable");
 if(!Array.isArray(hazards))return deny("hazards require an explicit list");
 const effects=has("effect_remaining_turns")?f.effect_remaining_turns.value:null;
 const expectedEffects=[...(weather!==""?["weather"]:[]),...(terrain!==""?["terrain"]:[])];
 if(expectedEffects.length){
   if(!["raindance"].includes(weather)&&weather!=="")
     return deny("unsupported nonempty weather effect");
   if(!["electricterrain"].includes(terrain)&&terrain!=="")
     return deny("unsupported nonempty terrain effect");
   if(!exact(effects,expectedEffects)||expectedEffects.some(k=>!limit(effects[k],8)))
     return deny("active weather or terrain requires exact observed remaining turns");
   if(!frameFor(["active_opponent","opponent_hp_exact","opponent_boosts",
      "weather","terrain","hazards","effect_remaining_turns"]))
     return deny("field duration lacks matching current-frame evidence");
 }else if(has("effect_remaining_turns")){
   return deny("effect duration with no supported weather or terrain");
 }
 if(hazards.length){
   if(Array.isArray(rawHazards)||rawHazards.turn_epoch!==
       obs.evidence.find(e=>e.id===f.request_state.evidence_ids?.[0])?.turn_epoch||
       rawHazards.state_revision!==
       obs.evidence.find(e=>e.id===f.request_state.evidence_ids?.[0])?.state_revision)
     return deny("hazards require current opponent-side and revision binding");
   if(hazards.length!==1||hazards[0]!=="stealthrock")
     return deny("only single opponent-side Stealth Rock is modeled");
   if(!frameFor(["active_opponent","opponent_hp_exact","opponent_boosts",
       "hazards","weather","terrain"]))
     return deny("hazard side lacks matching current-frame evidence");
 }
 // Unverified change histories must not slip into the created Battle.
 if(choice){
   if(!exact(choice,["move_id","item_id","turn_epoch","state_revision"])||
      !moveKey(choice.move_id)||!["choicescarf","choiceband","choicespecs"].includes(choice.item_id))
     return deny("choice lock needs an atomic move/item/current-turn contract");
   const frame=frameFor(["active_opponent","opponent_hp_exact","opponent_boosts",
      "choice_lock","opponent_item"]);
   if(!frame||frame.turn_epoch!==choice.turn_epoch||
     frame.state_revision!==choice.state_revision)
     return deny("choice lock does not match current frame");
   if(f.opponent_item.value!==choice.item_id)
     return deny("choice lock contradicts currently held item");
 }
 if(removed){
   const frame=frameFor(["active_opponent","opponent_hp_exact",
      "opponent_boosts","opponent_item"]);
   if(!frame||!historical("opponent_removed_item",
      ["item_id","cause","target_species","turn_epoch","state_revision"],frame))
     return deny("item removal lacks a linked observed historical event");
   if(removed.cause!=="knockoff"||removed.target_species!==f.opponent_species.value||
       !removed.item_id||!ctx.allowed.availableItems.has(removed.item_id)||
       ctx.dex.items.get(removed.item_id).megaStone)
     return deny("unsupported or illegal Knock Off item-removal event");
   if(f.opponent_item.value!==""||has("opponent_consumed_item")||choice)
     return deny("item removal contradicts current item, consumption, or lock");
 }
 if(mega||has("opponent_mega_state")&&f.opponent_mega_state.value===true){
   const frame=frameFor(["active_opponent","opponent_form","opponent_mega_state",
      "opponent_current_type","opponent_ability","opponent_item",
      "opponent_hp_exact","opponent_boosts"]);
   if(!frame||!historical("opponent_mega_history",
       ["from_form","to_form","stone_id","turn_epoch","state_revision"],frame))
     return deny("Mega requires linked history and current-form evidence");
   if(mega.from_form!=="garchomp"||mega.to_form!=="garchompmega"||
      mega.stone_id!=="garchompite"||f.opponent_form.value!=="garchompmega"||
      f.opponent_item.value!=="garchompite"||
      f.opponent_mega_state.value!==true||f.opponent_ability.value!=="sandforce")
     return deny("Mega history/form/item/ability contradict fixed Garchomp Mega");
   if(choice||removed||has("opponent_consumed_item"))
     return deny("Mega cannot coexist with alternate held-item history");
 }else if(has("opponent_mega_history")){
   return deny("Mega history supplied without confirmed current Mega");
 }
 return {choice,removed,mega,effects,weather,terrain,hazards,
   admitted:["choice_lock","opponent_removed_item","opponent_mega_history",
       "effect_remaining_turns"].filter(has)};
}
function apply(battle,active,plan){
 if(plan.mega){
   if(active.item!=="garchompite"||active.canMegaEvo!=="Garchomp-Mega"||
      !battle.actions.runMegaEvo(active)||active.species.id!=="garchompmega"||
      active.ability!=="sandforce"||active.canMegaEvo)
     return "Mega transition failed in actual hypothetical Battle";
 }
 if(plan.removed){
   if(active.item!==plan.removed.item_id||!active.setItem("")||active.item)
     return "original registered item differs from observed Knock Off removal";
 }
 if(plan.choice){
   if(active.item!==plan.choice.item_id||
      active.moveSlots.length!==1||
      active.moveSlots[0].id!==moveKey(plan.choice.move_id))
     return "choice item or locked move conflicts with hypothetical set";
   // Synthetic CURRENT-state injection only. No prior action or lastMove is
   // fabricated; Showdown's choicelock condition otherwise requires activeMove.
   active.volatiles.choicelock=battle.initEffectState({
     id:"choicelock",target:active,move:moveKey(plan.choice.move_id)});
 }
 if(plan.weather){
   if(!battle.field.setWeather(plan.weather,active))return "weather cannot be set";
   battle.field.weatherState.duration=plan.effects.weather;
 }
 if(plan.terrain){
   if(!battle.field.setTerrain(plan.terrain,active))return "terrain cannot be set";
   battle.field.terrainState.duration=plan.effects.terrain;
 }
 if(plan.hazards.length){
   if(!battle.p2.addSideCondition("stealthrock",active))
     return "opponent-side hazard cannot be represented";
 }
 return null;
}
function matches(battle,plan){
 const active=battle.p2.active[0];
 return (!plan.mega||(active.species.id==="garchompmega"&&
           active.ability==="sandforce"&&!active.canMegaEvo))&&
   (!plan.removed||active.item===""&&
     !active.volatiles.choicelock)&&
   (!plan.choice||(active.item===plan.choice.item_id&&
     active.volatiles.choicelock?.move===moveKey(plan.choice.move_id)))&&
   battle.field.weather===plan.weather&&
   battle.field.terrain===plan.terrain&&
   (!plan.weather||battle.field.weatherState.duration===plan.effects.weather)&&
   (!plan.terrain||battle.field.terrainState.duration===plan.effects.terrain)&&
   (plan.hazards.length===0||!!battle.p2.sideConditions.stealthrock);
}
module.exports={inspect,apply,matches};
