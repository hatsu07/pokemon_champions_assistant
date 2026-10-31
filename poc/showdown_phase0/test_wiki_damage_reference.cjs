'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {calculate,halfDownRatio}=require('./wiki_damage_reference.cjs');
const {allowedChampionsSets,validateChampionsScenario,SCENARIOS,FILLER}=require('./wiki_damage_compare.cjs');
const {cases, sources, schema}=JSON.parse(fs.readFileSync(path.join(__dirname,'wiki_damage_vectors.json'),'utf8'));
const neutral={attack:182,defense:150,basePower:100,powerMultiplier:[1,1],weatherMultiplier:[1,1],critical:false,stabMultiplier:[3,2],typeMultiplier:[2,1],otherMultiplier:[1,1]};
test('reference data has source URLs and independent wiki expectations',()=>{
  assert.equal(schema,'champions-wiki-reference-v1');
  for(const c of cases){
    assert.ok(c.source_ids.length);
    for(const id of c.source_ids)assert.ok(sources[id]?.url?.startsWith('https://'));
    assert.equal(c.comparison_scope,'PINNED_CHAMPIONS_COMPARABLE');
  }
  assert.equal(cases.filter(c=>c.comparison_scope==='PINNED_CHAMPIONS_COMPARABLE').length,1);
  assert.equal(cases.length,1);
  assert.ok(cases.every(c=>!/cresselia/i.test(c.id)));
});
for(const c of cases){test(c.id+' matches a published expected range',()=>{
  const r=calculate(c.input);
  assert.equal(r.minimum,c.expected_min);
  assert.equal(r.maximum,c.expected_max);
  assert.equal(r.rolls.length,16);
  assert.ok(r.rolls.every((v,i,a)=>i===0||v>=a[i-1]));
  assert.equal(r.status,'WIKI_ARITHMETIC_ONLY');
});}
test('five-drop-five-up rounding, not conventional half-up',()=>{
  assert.equal(halfDownRatio(65n,[3n,2n]),97n);
  assert.equal(halfDownRatio(55n,[3n,2n]),82n);
  assert.equal(halfDownRatio(51n,[3n,2n]),76n);
  assert.equal(halfDownRatio(3n,[5n,4n]),4n);
});
test('synthetic critical and held-item power multiplier: arithmetic only',()=>{
  const r=calculate({attack:100,defense:100,basePower:65,powerMultiplier:[3,2],weatherMultiplier:[1,1],critical:true,stabMultiplier:[3,2],typeMultiplier:[1,2],otherMultiplier:[1,1]});
  assert.deepEqual(r.rolls,[42,42,42,43,43,44,45,45,45,46,46,47,48,48,48,49]);
});
test('unknown inputs, extra fields, and invalid types fail closed',()=>{
  assert.throws(()=>calculate({...neutral,attack:null}),/attack/);
  assert.throws(()=>calculate({...neutral,powerMultiplier:undefined}),/powerMultiplier/);
  assert.throws(()=>calculate({...neutral,field:'rain'}),/unexpected/);
  assert.throws(()=>calculate({...neutral,defense:0}),/defense/);
  assert.throws(()=>calculate({...neutral,critical:'false'}),/critical/);
});
test('immunity is 0 damage and low effectiveness never returns negative',()=>{
  assert.deepEqual(new Set(calculate({...neutral,typeMultiplier:[0,1]}).rolls),new Set([0]));
  assert.ok(calculate({...neutral,attack:1,defense:999999,basePower:1,stabMultiplier:[1,1],typeMultiplier:[1,4]}).rolls.every(d=>d>=1));
});

test('real Champions master: all six team sets are legal and Metagross uses Bullet Punch',()=>{
  const {Dex}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
  const dex=Dex.forFormat('gen9championsbssregmc');
  const allowed=allowedChampionsSets(dex);
  const scenario=SCENARIOS['WIKI-EARTHQUAKE-GARCHOMP-METAGROSS'];
  const entry=allowed.species.get('metagross');
  assert.ok(entry);
  assert.ok(entry.learned.has('bulletpunch'));
  assert.ok(!entry.learned.has('tackle'));
  assert.deepEqual(scenario.defender.moves,['Bullet Punch']);
  const teams=validateChampionsScenario(scenario,dex,allowed);
  assert.equal(teams.length,2);
  assert.deepEqual(teams.map(t=>t.length),[3,3]);
  for(const team of teams)for(const mon of team){
    assert.ok(allowed.species.has(dex.species.get(mon.species).id));
    assert.ok(mon.moves.every(m=>allowed.species.get(dex.species.get(mon.species).id).learned.has(dex.moves.get(m).id)));
  }
});
test('real Champions master: unknown species, unequipped and illegal moves fail closed',()=>{
  const {Dex}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
  const dex=Dex.forFormat('gen9championsbssregmc');
  const allowed=allowedChampionsSets(dex);
  const s=SCENARIOS['WIKI-EARTHQUAKE-GARCHOMP-METAGROSS'];
  const withDefender=mon=>({...s,defender:{...s.defender,...mon}});
  assert.throws(()=>validateChampionsScenario(withDefender({moves:['Tackle']}),dex,allowed),
    /set move absent from Champions master and pinned learnset/);
  assert.throws(()=>validateChampionsScenario(withDefender({species:'Cresselia'}),dex,allowed),
    /not registered in Champions usable master/);
  assert.throws(()=>validateChampionsScenario({...s,move:'Dragon Claw'},dex,allowed),
    /attack move not equipped/);
  assert.throws(()=>validateChampionsScenario({...s,attacker:{...s.attacker,moves:['Tackle']}},dex,allowed),
    /set move absent from Champions master and pinned learnset/);
  assert.throws(()=>validateChampionsScenario({...s,bench:[{...FILLER,moves:['Tackle']},FILLER]},dex,allowed),
    /set move absent from Champions master and pinned learnset/);
});
test('real Champions master: ability/item/form and malformed moves fail closed',()=>{
  const {Dex}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
  const dex=Dex.forFormat('gen9championsbssregmc');
  const allowed=allowedChampionsSets(dex);
  const s=SCENARIOS['WIKI-EARTHQUAKE-GARCHOMP-METAGROSS'];
  const withAttacker=mon=>({...s,attacker:{...s.attacker,...mon}});
  const withDefender=mon=>({...s,defender:{...s.defender,...mon}});
  assert.throws(()=>validateChampionsScenario(withDefender({ability:'Rough Skin'}),dex,allowed),
    /ability absent\/unmapped/);
  assert.throws(()=>validateChampionsScenario(withAttacker({ability:'Clear Body'}),dex,allowed),
    /ability absent\/unmapped/);
  assert.throws(()=>validateChampionsScenario(withAttacker({item:'Unobtainium'}),dex,allowed),
    /item absent/);
  assert.throws(()=>validateChampionsScenario(withAttacker({species:'Garchomp-Mega'}),dex,allowed),
    /not registered in Champions usable master/);
  assert.throws(()=>validateChampionsScenario(withDefender({moves:[]}),dex,allowed),
    /invalid move slots/);
  assert.throws(()=>validateChampionsScenario(withDefender({moves:['Bullet Punch','Bullet Punch']}),dex,allowed),
    /duplicate move slot/);
  assert.throws(()=>validateChampionsScenario({...s,bench:[FILLER]},dex,allowed),
    /exactly two bench sets/);
});

test('Bulbapedia is preferred among community references; incomplete Champions damage rules cannot be certified',()=>{
  const registry=JSON.parse(fs.readFileSync(path.join(__dirname,'champions_wiki_sources.json'),'utf8'));
  assert.equal(registry.schema,'champions-community-reference-index-v1');
  assert.equal(registry.regulation,'M-C');
  assert.equal(registry.guarantees.game_equivalence,false);
  assert.equal(registry.guarantees.full_damage_coverage,false);
  assert.ok(registry.source_order.indexOf('BULBAPEDIA_CHAMPIONS_SPECIFIC') <
    registry.source_order.indexOf('OTHER_CHAMPIONS_WIKIS'));
  const references=new Map(registry.references.map(r=>[r.id,r]));
  for(const id of ['bulbapedia_champions_roster','bulbapedia_champions_moves',
    'bulbapedia_champions_stats','bulbapedia_knock_off','bulbapedia_protean']){
    assert.equal(references.get(id)?.status,'CHAMPIONS_SECTION_PRESENT');
    assert.ok(references.get(id)?.url?.startsWith('https://bulbapedia.bulbagarden.net/wiki/'));
  }
  assert.equal(references.get('bulbapedia_damage')?.status,'INCOMPLETE_FOR_CHAMPIONS');
  assert.equal(references.get('secondary_damage_order')?.priority,'SECONDARY_COMMUNITY');
});
