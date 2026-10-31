'use strict';
// Manual implementation of narrow, documented Champions-specific mechanics.
// No Showdown imports. Never call this from live ranking/battle decisions.
const STATUS='SOURCE_RULE_SIMULATION_ONLY';
const TYPES=new Set(['Normal','Fire','Water','Electric','Grass','Ice','Fighting','Poison','Ground',
  'Flying','Psychic','Bug','Rock','Ghost','Dragon','Dark','Steel','Fairy']);
const STAGES=['atk','def','spa','spd','spe','accuracy','evasion'];
const unsupported=(reason)=>({status:'UNSUPPORTED',reason});
function exact(value,keys,label){
  if(!value||typeof value!=='object'||Array.isArray(value)||
    Object.keys(value).some(k=>!keys.includes(k))||keys.some(k=>!Object.hasOwn(value,k)))
    throw Error('incomplete or unexpected '+label+' input');
}
function types(value){
  if(!Array.isArray(value)||value.length<1||value.length>2||
    value.some(t=>!TYPES.has(t))||new Set(value).size!==value.length)
    throw Error('invalid or unknown Champions type');
  return [...value];
}
function protean(state,event){
  exact(state,['types','used'],'protean state');
  if(typeof state.used!=='boolean')throw Error('unknown Protean use flag');
  types(state.types);
  if(!event||typeof event!=='object')throw Error('unknown Protean event');
  if(event.kind==='switch-in'){
    exact(event,['kind','baseTypes'],'Protean switch');
    return {status:STATUS,state:{types:types(event.baseTypes),used:false},rule_id:'PROTEAN_FIRST_MOVE_TYPE'};
  }
  if(event.kind==='mega-greninja'){
    exact(event,['kind','species'],'Protean mega');
    if(event.species!=='greninja'||!state.used)
      return unsupported('Only the documented post-Protean Greninja Mega case is supported');
    return {status:STATUS,state:{types:['Water','Dark'],used:false},rule_id:'PROTEAN_MEGA_GRENINJA_RESET'};
  }
  if(event.kind!=='use-move')return unsupported('Only successful ordinary move use has a rule contract');
  exact(event,['kind','moveType'],'Protean move');
  if(!TYPES.has(event.moveType))throw Error('unknown move type');
  return {status:STATUS,state:state.used?{types:[...state.types],used:true}:
    {types:[event.moveType],used:true},rule_id:'PROTEAN_FIRST_MOVE_TYPE'};
}
function knockOff(event){
  exact(event,['hit','heldItem','removable','attackerFaintedFromContact'],'Knock Off event');
  if(typeof event.hit!=='boolean'||typeof event.attackerFaintedFromContact!=='boolean'||
    !(event.heldItem===null||typeof event.heldItem==='string'&&event.heldItem.length>0))
    throw Error('unknown hit/item/attacker fate');
  if(!event.hit)return {status:STATUS,power_multiplier:[1,1],next_item:event.heldItem,
    rule_id:'KNOCK_OFF_HELD_ITEM',removed:false};
  if(event.heldItem===null)return {status:STATUS,power_multiplier:[1,1],next_item:null,
    rule_id:'KNOCK_OFF_HELD_ITEM',removed:false};
  if(event.removable!==true)return unsupported('Item cannot be verified removable in the stated conditions');
  return {status:STATUS,power_multiplier:[3,2],next_item:null,
    rule_id:'KNOCK_OFF_HELD_ITEM',removed:true};
}
function whiteHerb(state){
  exact(state,['item','stages'],'White Herb state');
  if(state.item!=='whiteherb'&&state.item!==null)
    return unsupported('Only White Herb or no held item is modeled');
  exact(state.stages,STAGES,'stat stages');
  for(const k of STAGES)if(!Number.isInteger(state.stages[k])||state.stages[k]<-6||state.stages[k]>6)
    throw Error('unknown or invalid stat stage: '+k);
  const neg=STAGES.some(k=>state.stages[k]<0);
  const consumed=state.item==='whiteherb'&&neg;
  return {status:STATUS,rule_id:'WHITE_HERB_ON_LOWERED_STATS',consumed,
    // This is a post-drop state contract, not a claim about exact event timing.
    state:{item:consumed?null:state.item,stages:Object.fromEntries(
      STAGES.map(k=>[k,consumed&&state.stages[k]<0?0:state.stages[k]]))}};
}
function choiceScarf(state,event){
  exact(state,['item','lockedMove'],'Choice Scarf state');
  if(state.item!=='choicescarf'&&state.item!==null)return unsupported('Unexpected held item');
  if(state.lockedMove!==null&&(typeof state.lockedMove!=='string'||!state.lockedMove))
    throw Error('unknown locked move');
  if(!event||typeof event!=='object')throw Error('unknown Choice Scarf event');
  if(event.kind==='switch-out'){
    exact(event,['kind'],'Choice Scarf switch');
    return {status:STATUS,rule_id:'CHOICE_SCARF_MOVE_LOCK',
      speed_multiplier:[state.item==='choicescarf'?3:1,state.item==='choicescarf'?2:1],
      state:{item:state.item,lockedMove:null},allowed:true};
  }
  if(event.kind!=='try-move'&&event.kind!=='use-move')
    return unsupported('Forced move use or item removal is not modeled');
  exact(event,['kind','move'],'Choice Scarf move');
  if(typeof event.move!=='string'||!event.move)throw Error('unknown move');
  const allowed=state.item!=='choicescarf'||!state.lockedMove||state.lockedMove===event.move;
  if(event.kind==='use-move'&&!allowed)
    return unsupported('Caller supplied an impossible executed move under Choice lock');
  const lockedMove=state.item==='choicescarf'&&event.kind==='use-move'?
    (state.lockedMove||event.move):state.lockedMove;
  return {status:STATUS,rule_id:'CHOICE_SCARF_MOVE_LOCK',
    speed_multiplier:state.item==='choicescarf'?[3,2]:[1,1],
    state:{item:state.item,lockedMove},allowed:!!allowed};
}
module.exports={STATUS,protean,knockOff,whiteHerb,choiceScarf,STAGES};
