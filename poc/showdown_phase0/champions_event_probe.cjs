'use strict';
// Isolated Phase 0 event diagnostics. Reference transitions never import Showdown.
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {protean,knockOff,whiteHerb,choiceScarf,STAGES,STATUS}=require('./champions_rule_reference.cjs');
const {moveKey}=require('./champions_move_key.cjs');
const {allowedChampionsSets,validateTeamConstraints}=require('./wiki_damage_compare.cjs');
const PIN='ccfaa20f32af363569cd21a9d501b2bc1ded26b1';
const FORMAT='gen9championsbssregmc';
const LEDGER=JSON.parse(fs.readFileSync(path.join(__dirname,'champions_rule_contracts.json'),'utf8'));
const RULES=new Map(LEDGER.rules.map(r=>[r.id,r]));
const JA={protean:'へんげんじざい',roughskin:'さめはだ',clearbody:'クリアボディ',
  innerfocus:'せいしんりょく',torrent:'げきりゅう',overgrow:'しんりょく',blaze:'もうか'};
const SEED=[1,2,3,4];
const zero=()=>Object.fromEntries(STAGES.map(k=>[k,0]));
const set=(species,ability,item,moves)=>({species,ability,item,level:50,nature:'Serious',
  evs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:2},moves});
const M=set('Meowscarada','Protean','',['Brick Break','Knock Off','Protect']);
const N=set('Greninja','Protean','Greninjite',['Ice Beam','Dark Pulse','Surf','Protect']);
const G=set('Garchomp','Rough Skin','',['Earthquake','Dragon Claw','Protect','Swords Dance']);
const L=set('Lucario','Inner Focus','White Herb',['Close Combat','Protect']);
const P=set('Primarina','Torrent','',['Surf','Calm Mind','Protect']);
const T=set('Metagross','Clear Body','',['Iron Defense','Protect']);
const V=set('Venusaur','Overgrow','',['Giga Drain']);
const B=set('Blastoise','Torrent','',['Surf']);
const C=set('Charizard','Blaze','',['Flamethrower']);
const team=(three)=>{
  const registered=[...three];
  for(const filler of [V,B,C,L,G,T]){
    if(registered.length===6)break;
    if(!registered.some(s=>s.species===filler.species))registered.push(filler);
  }
  return registered.map(s=>({...s,evs:{...s.evs},moves:[...s.moves]}));
};
function validate(registered,dex,allowed,validator){
  validateTeamConstraints(registered,dex,allowed);
  for(const mon of registered){
    const species=dex.species.get(mon.species);
    const entry=allowed.species.get(species.id);
    if(!entry||species.isMega||species.battleOnly)throw Error('unregistered starting species '+mon.species);
    const ability=dex.abilities.get(mon.ability);
    if(!ability.exists||!JA[ability.id]||!entry.abilities.has(JA[ability.id])||
       !Object.values(species.abilities).some(x=>dex.abilities.get(x).id===ability.id))
      throw Error('unverified Champions ability '+mon.species+'/'+mon.ability);
    if(mon.item&&!allowed.availableItems.has(dex.items.get(mon.item).id))
      throw Error('unverified Champions held item '+mon.item);
    const held=dex.items.get(mon.item);
    if(held.megaEvolves&&held.megaEvolves!==species.baseSpecies)
      throw Error('wrong Champions Mega Stone '+mon.species+'/'+mon.item);
    const sourceLearnset=dex.species.getLearnsetData(entry.learnsetKey)?.learnset||{};
    for(const name of mon.moves){
      const key=moveKey(name);
      if(!key||!allowed.availableMoves.has(key)||!entry.learned.has(key)||
         !Object.hasOwn(sourceLearnset,key)||!dex.moves.get(key).exists||dex.moves.get(key).id!==key)
        throw Error('unlearnable/unknown Champions move '+mon.species+'/'+name);
    }
  }
  const problems=validator.validateTeam(registered.map(s=>({...s,evs:{...s.evs},moves:[...s.moves]})));
  if(problems?.length)throw Error('pinned team validation failed: '+problems.join(' | '));
}
function projection(p){
  return {species:p.species.id,types:[...p.types],item:p.item||null,boosts:{...p.boosts},
    hp:p.hp,fainted:!!p.fainted,volatiles:Object.keys(p.volatiles).sort()};
}
function fieldDiff(expected,actual){
  const matches=[],mismatches=[];
  for(const [field,value] of Object.entries(expected)){
    const observed=actual[field];
    (JSON.stringify(value)===JSON.stringify(observed)?matches:mismatches).push(
      {field,expected:value,actual:observed===undefined?null:observed});
  }
  return {matches,mismatches};
}
function caseResult(id,rule,context){
  const source=RULES.get(rule);
  if(!source)throw Error('unknown contract '+rule);
  return {id,rule_id:rule,source:{url:source.champions_source,section:source.source_section,
    evidence:source.evidence,master:source.master||null},not_verified:source.not_verified,
    ...context};
}
function compare(id,rule,{before,action,reference,expected,actual,events,precondition=true,
  reason=null,unverified=[],auxiliary_status=null}){
  const diff=fieldDiff(expected||{},actual||{});
  const valid=precondition&&reference?.status===STATUS&&reason===null&&
    actual!==undefined&&actual!==null&&expected&&Object.keys(expected).length>0;
  return caseResult(id,rule,{before,action,reference_result:reference||null,
    reference_state:reference?.state||null,
    expected_state:expected||null,actual_state:actual||null,events:events||[],
    matched_fields:diff.matches,mismatched_fields:valid?diff.mismatches:[],
    unverified_conditions:unverified,
    auxiliary_status:valid?null:auxiliary_status||
      (reference?.status==='UNSUPPORTED'?'UNSUPPORTED':'UNKNOWN'),
    reason:valid?null:reason||reference?.reason||'Precondition not observed',
    status:!valid?'INCONCLUSIVE':diff.mismatches.length?'WIKI_SPEC_MISMATCH':'WIKI_SPEC_MATCH'});
}
function make(dex,validator,allowed,p1,p2,seed=SEED){
  const a=team(p1),d=team(p2);
  validate(a,dex,allowed,validator);validate(d,dex,allowed,validator);
  const {Battle}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
  const b=new Battle({formatid:FORMAT,seed,strictChoices:true,
    p1:{name:'Synthetic A',team:a},p2:{name:'Synthetic B',team:d}});
  if(b.requestState==='teampreview')b.makeChoices('team 123','team 123');
  if(b.format.mod!=='champions'||b.p1.pokemon.length!==3||b.p2.pokemon.length!==3)
    throw Error('pinned format/team selection inactive');
  return b;
}
function turn(b,p1,p2){
  if(b.requestState!=='move')return {events:[],reason:'No move request: '+b.requestState};
  const at=b.log.length;
  try{b.makeChoices(p1,p2);}catch(e){return {events:[],reason:'Showdown rejected choice: '+e.message};}
  return {events:b.log.slice(at).filter(x=>!x.startsWith('|t:|'))};
}
const usedMove=(events,name)=>events.some(x=>x.startsWith('|move|p1a:')&&
  x.split('|')[3]===name&&!x.includes('[notarget]'));
const typeLog=(events)=>events.findIndex(x=>x.startsWith('|-start|p1a:')&&x.includes('|typechange|')&&x.includes('Protean'));
const damageLog=(events)=>events.findIndex(x=>x.startsWith('|-damage|p2a:'));
function normalMoveReason(out,name){
  if(out.reason)return out.reason;
  if(!usedMove(out.events,name))return 'Chosen move did not execute';
  if(out.events.some(x=>x.startsWith('|-fail|p1a:')||x.startsWith('|-immune|p2a:')))
    return 'Move failed or target was immune';
  if(damageLog(out.events)<0)return 'No direct hit on target was observed';
  return null;
}
function proteanCases(dex,validator,allowed,seed){
  const b=make(dex,validator,allowed,[M,P,L],[T,G,B],seed);
  const results=[];let state={types:['Grass','Dark'],used:false};
  for(const [id,choice,name,type,expectedActivation] of [
    ['first-move','move 1','Brick Break','Fighting',true],
    ['second-move','move 2','Knock Off','Dark',false]]){
    const before=projection(b.p1.active[0]);
    const reference=protean(state,{kind:'use-move',moveType:type});
    const out=turn(b,choice,'move 1');const after=projection(b.p1.active[0]);
    const idx=typeLog(out.events),damage=damageLog(out.events);
    const expected={types:reference.state.types,protean_activation:expectedActivation};
    const actual={...after,protean_activation:idx>=0};
    if(expectedActivation&&damage>=0){expected.activation_before_damage=true;
      actual.activation_before_damage=idx>=0&&idx<damage;}
    const reason=normalMoveReason(out,name)||
      (before.species!=='meowscarada'||JSON.stringify(before.types)!==JSON.stringify(state.types)?
        'Protean pre-state differs from independent reference':null);
    results.push(compare('protean-'+id,'PROTEAN_FIRST_MOVE_TYPE',
      {before,action:{p1:choice,p2:'move 1',move:name},reference,expected,actual,
        events:out.events,reason,unverified:['Direct internal Protean charge is not exposed; inferred from later action']}));
    if(reason||results.at(-1).status==='WIKI_SPEC_MISMATCH')return results;
    state=reference.state;
  }
  const beforeSwitch=projection(b.p1.active[0]);
  const out1=turn(b,'switch 2','move 1');
  const out2=turn(b,'switch 2','move 1'); // active slot 2 is the original Meowscarada
  const before=projection(b.p1.active[0]);
  const reference=protean(state,{kind:'switch-in',baseTypes:['Grass','Dark']});
  results.push(compare('protean-switch-reset','PROTEAN_FIRST_MOVE_TYPE',{
    before:{...beforeSwitch,reference_state:state},
    action:{p1:['switch 2','switch 2'],p2:['move 1','move 1']},reference,
    expected:{types:reference.state.types,species:'meowscarada'},actual:before,
    events:out2.events,reason:out1.reason||out2.reason||
      (before.species!=='meowscarada'?'Original user did not re-enter':null),
    unverified:['Charge reset is confirmed only by the next action']}));
  if(results.at(-1).status==='INCONCLUSIVE')return results;
  state=reference.state;
  const pre=projection(b.p1.active[0]);
  const again=protean(state,{kind:'use-move',moveType:'Dark'});
  const out3=turn(b,'move 2','move 1');
  const after=projection(b.p1.active[0]);
  results.push(compare('protean-after-switch','PROTEAN_FIRST_MOVE_TYPE',{
    before:pre,action:{p1:'move 2',p2:'move 1',move:'Knock Off'},reference:again,
    expected:{types:again.state.types,protean_activation:true},
    actual:{...after,protean_activation:typeLog(out3.events)>=0},events:out3.events,
    reason:normalMoveReason(out3,'Knock Off')||
      (JSON.stringify(pre.types)!==JSON.stringify(state.types)?'Switch-in pre-state mismatch':null)}));
  const ambiguous=protean({types:['Dark'],used:false},{kind:'use-move',moveType:'Dark'});
  results.push(compare('protean-same-type-unknown','PROTEAN_FIRST_MOVE_TYPE',{
    before:{types:['Dark'],used:false},action:{kind:'use-move',moveType:'Dark'},
    reference:ambiguous,reason:ambiguous.reason,auxiliary_status:'UNKNOWN',
    unverified:['Same-type charge consumption is not specified in cited Champions text']}));
  const failed=protean({types:['Grass','Dark'],used:false},{kind:'failed-move'});
  results.push(compare('protean-failed-move','PROTEAN_FIRST_MOVE_TYPE',{
    before:{types:['Grass','Dark'],used:false},action:{kind:'failed-move'},
    reference:failed,reason:failed.reason,
    unverified:['Failed-move timing and dynamic move types are unsupported']}));
  return results;
}
function megaCases(dex,validator,allowed,seed){
  const b=make(dex,validator,allowed,[N,P,L],[T,G,B],seed);
  const beforeFirst=projection(b.p1.active[0]);
  const first=turn(b,'move 1','move 1');
  const firstRef=protean({types:['Water','Dark'],used:false},{kind:'use-move',moveType:'Ice'});
  const results=[compare('mega-prior-protean','PROTEAN_MEGA_GRENINJA_RESET',{
    before:beforeFirst,action:{p1:'move 1',p2:'move 1',move:'Ice Beam'},
    reference:firstRef,expected:{types:['Ice'],protean_activation:true},
    actual:{...projection(b.p1.active[0]),protean_activation:typeLog(first.events)>=0},
    events:first.events,reason:normalMoveReason(first,'Ice Beam')||
      (beforeFirst.species!=='greninja'||beforeFirst.item!=='greninjite'||
       JSON.stringify(beforeFirst.types)!==JSON.stringify(['Water','Dark'])?
        'Greninja initial state differs from source fixture':null)})];
  if(results[0].status!=='WIKI_SPEC_MATCH')return results;
  const before=projection(b.p1.active[0]);
  const megaRef=protean(firstRef.state,{kind:'mega-greninja',species:'greninja'});
  let afterMega=null;
  const original=b.actions.runMegaEvo;
  if(typeof original!=='function')return [...results,compare('mega-reset','PROTEAN_MEGA_GRENINJA_RESET',{
    before,action:{p1:'move 2 mega',p2:'move 1'},reference:megaRef,
    reason:'Pinned simulator does not expose Mega transition hook',unverified:['Mega intermediate state']})];
  b.actions.runMegaEvo=function(...args){const result=original.apply(this,args);
    afterMega=projection(b.p1.active[0]);return result;};
  const out=turn(b,'move 2 mega','move 1');
  b.actions.runMegaEvo=original;
  const next=protean(megaRef.state,{kind:'use-move',moveType:'Dark'});
  const actual=projection(b.p1.active[0]);
  results.push(compare('mega-reset','PROTEAN_MEGA_GRENINJA_RESET',{
    before,action:{p1:'move 2 mega',p2:'move 1',move:'Dark Pulse'},reference:megaRef,
    expected:{mega_types:megaRef.state.types,post_move_types:next.state.types,
      species:'greninjamega',protean_reactivation:true},
    actual:{mega_types:afterMega?.types||null,post_move_types:actual.types,
      species:actual.species,protean_reactivation:typeLog(out.events)>=0},
    events:out.events,reason:normalMoveReason(out,'Dark Pulse')||
      (JSON.stringify(before.types)!==JSON.stringify(firstRef.state.types)?
        'Pre-Mega typing differs from reference':null)||
      (!afterMega?'Mega transition was not observed':null),
    unverified:['Direct internal ability charge after Mega is inferred from reactivation']}));
  return results;
}
function knockCases(dex,validator,allowed,seed){
  const results=[];
  for(const [id,item,lowHp] of [['held-item','Rocky Helmet',false],
    ['no-item','',false],['contact-faint','Rocky Helmet',true]]){
    const target={...G,item};
    const b=make(dex,validator,allowed,[M,P,L],[target,T,B],seed);
    if(lowHp)b.p1.active[0].hp=1; // synthetic contact-retaliation precondition
    const before={attacker:projection(b.p1.active[0]),defender:projection(b.p2.active[0])};
    const reference=knockOff({hit:true,heldItem:item?moveItem(item):null,
      removable:item?true:null,attackerFaintedFromContact:lowHp});
    const out=turn(b,'move 2','move 4');
    const after={attacker:projection(b.p1.pokemon[0]),defender:projection(b.p2.pokemon[0])};
    const actual={defender_item:after.defender.item,removed:!!item&&!after.defender.item,
      attacker_fainted:after.attacker.fainted};
    const expected={defender_item:reference.next_item,removed:reference.removed,
      attacker_fainted:lowHp};
    const reason=normalMoveReason(out,'Knock Off')||
      (before.attacker.species!=='meowscarada'||
       before.defender.item!==(item?moveItem(item):null)?
        'Knock Off pre-state differs from fixed fixture':null)||
      (lowHp&&!out.events.some(x=>x.includes('Rough Skin')||x.includes('Rocky Helmet'))?
        'Contact retaliation not observed':null);
    results.push(compare('knock-'+id,'KNOCK_OFF_HELD_ITEM',{
      before,action:{p1:'move 2',p2:'move 4',move:'Knock Off'},reference,
      expected,actual,events:out.events,reason,
      unverified:['Power multiplier and exact damage ordering are not inferred from one random hit']}));
  }
  const reference=knockOff({hit:true,heldItem:'unverified-item',removable:null,
    attackerFaintedFromContact:false});
  results.push(compare('knock-unknown-removability','KNOCK_OFF_HELD_ITEM',{
    before:null,action:null,reference,reason:reference.reason,
    unverified:['No valid simulator turn may be claimed for unverified removability']}));
  results.push(compare('knock-power-multiplier','KNOCK_OFF_HELD_ITEM',{
    before:null,action:{kind:'compare-power-multiplier'},reference:{status:'UNSUPPORTED'},
    reason:'One stochastic turn does not isolate the 1.5 power multiplier',
    unverified:['Requires an independent damage distribution or instrumented power observation']}));
  return results;
}
function moveItem(name){return name.toLowerCase().replace(/[^a-z0-9]/g,'');}
function herbCases(dex,validator,allowed,seed){
  const b=make(dex,validator,allowed,[L,P,M],[P,G,T],seed);
  const results=[];let referenceState={item:'whiteherb',stages:zero()};
  for(const [id,item] of [['first-drop','whiteherb'],['second-drop',null]]){
    const before=projection(b.p1.active[0]);
    const postDrop={...referenceState.stages,
      def:referenceState.stages.def-1,spd:referenceState.stages.spd-1};
    const reference=whiteHerb({item,stages:postDrop});
    const out=turn(b,'move 1','move 2'); // Primarina Calm Mind
    const after=projection(b.p1.active[0]);
    results.push(compare('herb-'+id,'WHITE_HERB_ON_LOWERED_STATS',{
      before,action:{p1:'move 1',p2:'move 2',move:'Close Combat'},reference,
      expected:{item:reference.state.item,boosts:reference.state.stages},actual:after,
      events:out.events,reason:normalMoveReason(out,'Close Combat')||
        (before.item!==referenceState.item||
         JSON.stringify(before.boosts)!==JSON.stringify(referenceState.stages)?
          'White Herb pre-state differs from independent reference':null)||
        (after.species!=='lucario'?'Lucario did not remain active':null),
      unverified:['Precise intermediate activation frame is not compared']}));
    referenceState=reference.state;
    if(results.at(-1).status==='INCONCLUSIVE')break;
  }
  return results;
}
function scarfCases(dex,validator,allowed,seed){
  const b=make(dex,validator,allowed,[{...G,item:'Choice Scarf'},P,L],[T,G,B],seed);
  const results=[];let state={item:'choicescarf',lockedMove:null};
  const a=b.p1.active[0],baseSpeed=a.baseStoredStats.spe,shownSpeed=a.getStat('spe');
  const before=projection(a);
  const reference=choiceScarf(state,{kind:'use-move',move:'Earthquake'});
  const out=turn(b,'move 1','move 1');
  const after=projection(b.p1.active[0]);
  const lock=b.p1.active[0].volatiles.choicelock?.move||null;
  results.push(compare('scarf-first-lock','CHOICE_SCARF_MOVE_LOCK',{
    before:{...before,base_speed:baseSpeed,effective_speed:shownSpeed},
    action:{p1:'move 1',p2:'move 1',move:'Earthquake'},reference,
    expected:{locked_move:'earthquake',speed_within_source_multiplier:true},
    actual:{locked_move:lock,speed_within_source_multiplier:
      shownSpeed>=Math.floor(baseSpeed*1.5)&&shownSpeed<=Math.ceil(baseSpeed*1.5)},
    events:out.events,reason:normalMoveReason(out,'Earthquake')||
      (before.species!=='garchomp'||before.item!=='choicescarf'||
       before.volatiles.includes('choicelock')?'Choice Scarf pre-state mismatch':null),
    unverified:['Exact Speed rounding with other modifiers']}));
  if(results[0].status!=='WIKI_SPEC_MATCH')return results;
  state=reference.state;
  const tryOther=choiceScarf(state,{kind:'try-move',move:'Dragon Claw'});
  const request=b.p1.activeRequest?.active?.[0];
  const other=request?.moves?.find(m=>m.id==='dragonclaw');
  results.push(compare('scarf-other-move','CHOICE_SCARF_MOVE_LOCK',{
    before:after,action:{kind:'inspect-next-move-request',move:'Dragon Claw'},reference:tryOther,
    expected:{allowed:false},actual:{allowed:other?!other.disabled:null},
    reason:!request||!other?'Move request not available':
      (after.item!=='choicescarf'||lock!==state.lockedMove?'Move-lock pre-state mismatch':null),
    unverified:['A disabled choice is inspected after a real turn; no illegal command is submitted']}));
  const sw1=turn(b,'switch 2','move 1');
  const sw2=turn(b,'switch 2','move 1');
  const switched=choiceScarf(state,{kind:'switch-out'});
  const active=projection(b.p1.active[0]);
  const newRequest=b.p1.activeRequest?.active?.[0];
  results.push(compare('scarf-switch-unlock','CHOICE_SCARF_MOVE_LOCK',{
    before:after,action:{p1:['switch 2','switch 2'],p2:['move 1','move 1']},reference:switched,
    expected:{species:'garchomp',locked_move:null,other_move_available:true},
    actual:{species:active.species,locked_move:b.p1.active[0].volatiles.choicelock?.move||null,
      other_move_available:!!newRequest?.moves?.find(m=>m.id==='dragonclaw'&&!m.disabled)},
    events:[...sw1.events,...sw2.events],reason:sw1.reason||sw2.reason||
      (active.species!=='garchomp'?'Scarf holder did not re-enter':null)}));
  return results;
}
function main(seed=SEED){
  if(!Array.isArray(seed)||seed.length!==4||seed.some(n=>!Number.isInteger(n)||n<0||n>65535))
    throw Error('invalid deterministic Showdown seed');
  const root=process.env.SHOWDOWN_ROOT;
  if(!root)throw Error('SHOWDOWN_ROOT required');
  const sha=execFileSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
  if(sha!==PIN)throw Error('Pinned Showdown SHA mismatch: '+sha);
  const {Dex,TeamValidator}=require(path.join(root,'dist','sim','index.js'));
  const dex=Dex.forFormat(FORMAT),validator=TeamValidator.get(FORMAT);
  if(dex.currentMod!=='champions')throw Error('Champions mod inactive');
  const allowed=allowedChampionsSets(dex);
  const results=[...proteanCases(dex,validator,allowed,seed),...megaCases(dex,validator,allowed,seed),
    ...knockCases(dex,validator,allowed,seed),...herbCases(dex,validator,allowed,seed),
    ...scarfCases(dex,validator,allowed,seed)];
  const counts=Object.fromEntries(['WIKI_SPEC_MATCH','WIKI_SPEC_MISMATCH','INCONCLUSIVE'].map(s=>
    [s,results.filter(r=>r.status===s).length]));
  return {schema:'champions-showdown-event-diagnostics-v1',scope:'OFFLINE_SYNTHETIC_ONLY',
    pinned_sha:PIN,format:FORMAT,seed,source_checked_at:LEDGER.checked_at,
    gate_A:'UNKNOWN',gate_B:'UNSUPPORTED',adoption:'NO_GO',counts,results};
}
if(require.main===module){
  try{process.stdout.write(JSON.stringify(main(),null,2)+'\n');}
  catch(e){process.stdout.write(JSON.stringify({status:'NOT_RUN',reason:e.message})+'\n');process.exitCode=2;}
}
module.exports={main,compare,validate,fieldDiff};
