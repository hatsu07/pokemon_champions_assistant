'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {protean,knockOff,whiteHerb,choiceScarf,STAGES,STATUS}=require('./champions_rule_reference.cjs');
const {rows}=require('./audit.cjs');
const base=process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,'masters');
const read=name=>rows(fs.readFileSync(path.join(base,name),'utf8'));
const initStages=()=>Object.fromEntries(STAGES.map(k=>[k,0]));
test('source ledger: separate source contracts, evidence types and unresolved exceptions',()=>{
  const d=JSON.parse(fs.readFileSync(path.join(__dirname,'champions_rule_contracts.json'),'utf8'));
  assert.equal(d.schema,'champions-source-rule-contracts-v1');
  assert.equal(d.regulation,'M-C');
  assert.equal(d.actual_game_compatibility,'UNKNOWN');
  assert.equal(d.showdown_event_compatibility,'NOT_TESTED');
  assert.equal(d.rules.length,5);
  for(const r of d.rules){
    assert.ok(r.id&&r.subject&&r.claims.length&&r.not_verified.length);
    assert.ok(r.champions_source.startsWith('https://bulbapedia.bulbagarden.net/wiki/'));
    assert.ok(r.source_section.includes('Champions')||r.source_section.includes('Champs'));
  }
});
test('source ledger: named rules exist in pinned Champions masters',()=>{
  const moves=read('Pokemon_Champions_MC_move_master.csv');
  const held=read('Pokemon_Champions_MC_usable_held_items.csv');
  const abilities=read('Pokemon_Champions_MC_usable_pokemon.csv');
  const move=moves.find(x=>x.MoveKey==='knockoff');
  assert.equal(move?.['技名'],'はたきおとす');
  assert.equal(move?.['威力'],'65');
  for(const [name,ja] of [['Choice Scarf','こだわりスカーフ'],['White Herb','しろいハーブ']])
    assert.ok(held.some(x=>x['英語名']===name&&x['道具名']===ja));
  assert.ok(abilities.some(x=>x['ポケモン名']==='マスカーニャ'&&x['特性'].includes('へんげんじざい')));
});
test('Protean: first documented move changes type once, second does not',()=>{
  let s={types:['Grass','Dark'],used:false};
  const one=protean(s,{kind:'use-move',moveType:'Ice'});
  assert.equal(one.status,STATUS);
  assert.deepEqual(one.state,{types:['Ice'],used:true});
  const two=protean(one.state,{kind:'use-move',moveType:'Dark'});
  assert.deepEqual(two.state,{types:['Ice'],used:true});
  assert.deepEqual(s,{types:['Grass','Dark'],used:false},'reference function is pure');
});
test('Protean: switch-in resets use count and base typing',()=>{
  const one=protean({types:['Ice'],used:true},{kind:'switch-in',baseTypes:['Grass','Dark']});
  assert.deepEqual(one.state,{types:['Grass','Dark'],used:false});
  assert.deepEqual(protean(one.state,{kind:'use-move',moveType:'Fire'}).state,{types:['Fire'],used:true});
});
test('Protean: Champions-specific Mega Greninja resets typing and Protean charge',()=>{
  const one=protean({types:['Ice'],used:true},{kind:'mega-greninja',species:'greninja'});
  assert.deepEqual(one.state,{types:['Water','Dark'],used:false});
  assert.deepEqual(protean(one.state,{kind:'use-move',moveType:'Water'}).state,{types:['Water'],used:true});
  assert.equal(protean({types:['Ice'],used:true},{kind:'mega-greninja',species:'meowscarada'}).status,'UNSUPPORTED');
});
test('Protean: ambiguous move events and missing type remain unhandled',()=>{
  assert.equal(protean({types:['Dark'],used:false},{kind:'failed-move'}).status,'UNSUPPORTED');
  assert.throws(()=>protean({types:['Dark'],used:false},{kind:'use-move'}),/incomplete/);
  assert.throws(()=>protean({types:['Dark'],used:null},{kind:'use-move',moveType:'Dark'}),/unknown/);
});
test('Knock Off: removable item grants 1.5 power then is removed',()=>{
  const r=knockOff({hit:true,heldItem:'whiteherb',removable:true,attackerFaintedFromContact:false});
  assert.equal(r.status,STATUS);
  assert.deepEqual(r.power_multiplier,[3,2]);
  assert.equal(r.next_item,null);
  assert.equal(r.removed,true);
});
test('Knock Off: held item is still removed on post-contact attacker faint',()=>{
  const r=knockOff({hit:true,heldItem:'whiteherb',removable:true,attackerFaintedFromContact:true});
  assert.equal(r.removed,true);
  assert.equal(r.next_item,null);
});
test('Knock Off: empty item and non-contact leaves ordinary power',()=>{
  const miss=knockOff({hit:false,heldItem:'whiteherb',removable:true,attackerFaintedFromContact:false});
  assert.deepEqual(miss.power_multiplier,[1,1]);
  assert.equal(miss.next_item,'whiteherb');
  const empty=knockOff({hit:true,heldItem:null,removable:null,attackerFaintedFromContact:false});
  assert.deepEqual(empty.power_multiplier,[1,1]);
  assert.equal(empty.removed,false);
});
test('Knock Off: non-removable or unverified condition is fail closed',()=>{
  for(const removable of [null,false]){
    const r=knockOff({hit:true,heldItem:'leftovers',removable,attackerFaintedFromContact:false});
    assert.equal(r.status,'UNSUPPORTED');
  }
  assert.throws(()=>knockOff({hit:true,heldItem:'leftovers'}),/incomplete/);
});
test('White Herb: consume on negative stages, clear all negatives, retain positives',()=>{
  const stages={...initStages(),atk:-2,def:1,spd:-1};
  const r=whiteHerb({item:'whiteherb',stages});
  assert.equal(r.status,STATUS);
  assert.equal(r.consumed,true);
  assert.equal(r.state.item,null);
  assert.deepEqual(r.state.stages,{...initStages(),def:1});
  assert.equal(stages.atk,-2,'pure reference');
});
test('White Herb: not consumed if no lowered stage; cannot activate twice',()=>{
  const r=whiteHerb({item:'whiteherb',stages:{...initStages(),atk:2}});
  assert.equal(r.consumed,false);
  assert.equal(r.state.item,'whiteherb');
  const absent=whiteHerb({item:null,stages:{...initStages(),atk:-2}});
  assert.equal(absent.consumed,false);
  assert.equal(absent.state.stages.atk,-2);
});
test('White Herb: unknown stages are not silently treated as neutral',()=>{
  assert.throws(()=>whiteHerb({item:'whiteherb',stages:{atk:-1}}),/incomplete/);
  assert.throws(()=>whiteHerb({item:'whiteherb',stages:{...initStages(),atk:null}}),/unknown or invalid/);
});
test('Choice Scarf: Speed 1.5; lock after executed move, not upon trying it',()=>{
  const initial={item:'choicescarf',lockedMove:null};
  const queried=choiceScarf(initial,{kind:'try-move',move:'knockoff'});
  assert.deepEqual(queried.speed_multiplier,[3,2]);
  assert.equal(queried.state.lockedMove,null);
  const used=choiceScarf(initial,{kind:'use-move',move:'knockoff'});
  assert.equal(used.state.lockedMove,'knockoff');
  assert.equal(choiceScarf(used.state,{kind:'try-move',move:'knockoff'}).allowed,true);
  assert.equal(choiceScarf(used.state,{kind:'try-move',move:'uturn'}).allowed,false);
});
test('Choice Scarf: switch-out unlocks move and new entry may lock another move',()=>{
  const out=choiceScarf({item:'choicescarf',lockedMove:'knockoff'},{kind:'switch-out'});
  assert.equal(out.state.lockedMove,null);
  assert.equal(choiceScarf(out.state,{kind:'use-move',move:'uturn'}).state.lockedMove,'uturn');
});
test('Choice Scarf: no item never locks; forced executed move conflict unmodeled',()=>{
  assert.equal(choiceScarf({item:null,lockedMove:null},{kind:'use-move',move:'knockoff'}).state.lockedMove,null);
  assert.equal(choiceScarf({item:'choicescarf',lockedMove:'knockoff'},{kind:'use-move',move:'uturn'}).status,'UNSUPPORTED');
  assert.equal(choiceScarf({item:'choicescarf',lockedMove:null},{kind:'item-removed'}).status,'UNSUPPORTED');
});
