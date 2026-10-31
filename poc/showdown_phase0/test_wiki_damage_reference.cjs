'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {calculate,halfDownRatio}=require('./wiki_damage_reference.cjs');
const {allowedChampionsSets,validateChampionsScenario,validateTeamConstraints,SCENARIOS}=require('./wiki_damage_compare.cjs');
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


function actualDex(){
  const {Dex,TeamValidator}=require(path.join(process.env.SHOWDOWN_ROOT,'dist','sim','index.js'));
  const dex=Dex.forFormat('gen9championsbssregmc');
  return {dex,validator:TeamValidator.get('gen9championsbssregmc'),allowed:allowedChampionsSets(dex)};
}
const SCENARIO=SCENARIOS['WIKI-EARTHQUAKE-GARCHOMP-METAGROSS'];
test('real masters: both full registered six-member teams pass set and format checks',()=>{
  const {dex,allowed,validator}=actualDex();
  assert.ok(allowed.ambiguous.has('squawkabilly'));
  assert.ok(!allowed.species.has('squawkabilly'));
  assert.ok(allowed.species.get('metagross').learned.has('bulletpunch'));
  assert.ok(!allowed.species.get('metagross').learned.has('tackle'));
  assert.deepEqual(SCENARIO.defender.moves,['Bullet Punch']);
  const teams=validateChampionsScenario(SCENARIO,dex,allowed);
  assert.deepEqual(teams.map(team=>team.length),[6,6]);
  assert.deepEqual(teams.map(team=>team.slice(0,3).map(m=>m.species)),[
    ['Garchomp','Metagross','Venusaur'],['Metagross','Garchomp','Venusaur']]);
  assert.notEqual(teams[0][2],teams[1][2],'teams must not share set objects');
  assert.notEqual(teams[0][2].evs,teams[1][2].evs,'no shared mutable EV objects');
  for(const team of teams){
    assert.equal(validator.validateTeam(structuredClone(team)),null);
    assert.equal(new Set(team.map(m=>allowed.species.get(dex.species.get(m.species).id).nationalNo)).size,6);
  }
});
test('real masters: unavailable species, illegal moves, unselected moves, and bench are rejected',()=>{
  const {dex,allowed}=actualDex();
  const s=SCENARIO;
  assert.throws(()=>validateChampionsScenario({...s,defender:{...s.defender,moves:['Tackle']}},dex,allowed),
    /set move absent from Champions master and pinned learnset/);
  assert.throws(()=>validateChampionsScenario({...s,defender:{...s.defender,species:'Cresselia'}},dex,allowed),
    /unknown Champions base species identity/);
  assert.throws(()=>validateChampionsScenario({...s,move:'Dragon Claw'},dex,allowed),
    /attack move not equipped/);
  assert.throws(()=>validateChampionsScenario({...s,attacker:{...s.attacker,moves:['Tackle']}},dex,allowed),
    /set move absent from Champions master and pinned learnset/);
  assert.throws(()=>validateChampionsScenario({...s,p1bench:[{...s.p1bench[0],moves:['Tackle']},...s.p1bench.slice(1)]},dex,allowed),
    /set move absent from Champions master and pinned learnset/);
});
test('real masters: abilities, items, unsupported forms, malformed moves fail closed',()=>{
  const {dex,allowed}=actualDex();
  const s=SCENARIO;
  const attacker=fields=>({...s,attacker:{...s.attacker,...fields}});
  const defender=fields=>({...s,defender:{...s.defender,...fields}});
  assert.throws(()=>validateChampionsScenario(defender({ability:'Rough Skin'}),dex,allowed),/ability absent\/unmapped/);
  assert.throws(()=>validateChampionsScenario(attacker({ability:'Clear Body'}),dex,allowed),/ability absent\/unmapped/);
  assert.throws(()=>validateChampionsScenario(attacker({item:'Unobtainium'}),dex,allowed),/item absent/);
  assert.throws(()=>validateChampionsScenario(attacker({species:'Garchomp-Mega'}),dex,allowed),/unknown Champions base species identity/);
  assert.throws(()=>validateChampionsScenario(defender({moves:[]}),dex,allowed),/invalid move slots/);
  assert.throws(()=>validateChampionsScenario(defender({moves:['Bullet Punch','Bullet Punch']}),dex,allowed),/duplicate move slot/);
  assert.throws(()=>validateChampionsScenario({...s,p1bench:[s.p1bench[0]]},dex,allowed),/two independent five-member benches/);
});
test('registered team cannot repeat a species; opponent may use the same species',()=>{
  const {dex,allowed}=actualDex();
  const [p1,p2]=validateChampionsScenario(SCENARIO,dex,allowed);
  assert.doesNotThrow(()=>validateTeamConstraints(p1,dex,allowed));
  assert.doesNotThrow(()=>validateTeamConstraints(p2,dex,allowed));
  const duplicate=[p1[0],{...p1[0]},...p1.slice(2)];
  assert.throws(()=>validateTeamConstraints(duplicate,dex,allowed),/duplicate Champions named species/);
  assert.throws(()=>validateChampionsScenario({...SCENARIO,p1bench:[p1[0],...SCENARIO.p1bench.slice(1)]},dex,allowed),
    /duplicate Champions named species/);
});
test('regional form and regular form share the same Champions species identity',()=>{
  const {dex,allowed}=actualDex();
  const [p1]=validateChampionsScenario(SCENARIO,dex,allowed);
  // Both Raichu forms are in the actual M-C master under National No. #0026.
  assert.equal(allowed.species.get('raichu')?.nationalNo,'#0026');
  assert.equal(allowed.species.get('raichualola')?.nationalNo,'#0026');
  const team=[{...p1[0],species:'Raichu'},{...p1[1],species:'Raichu-Alola'},...p1.slice(2)];
  assert.throws(()=>validateTeamConstraints(team,dex,allowed),/duplicate Champions named species/);
});
test('registered team item IDs must be unique; empty item may repeat',()=>{
  const {dex,allowed}=actualDex();
  const [p1]=validateChampionsScenario(SCENARIO,dex,allowed);
  assert.doesNotThrow(()=>validateTeamConstraints(p1,dex,allowed)); // all six have empty item
  assert.ok(allowed.availableItems.has('leftovers'));
  assert.ok(allowed.availableItems.has('sitrusberry'));
  const duplicate=[{...p1[0],item:'Leftovers'},{...p1[1],item:'Leftovers'},...p1.slice(2)];
  assert.throws(()=>validateTeamConstraints(duplicate,dex,allowed),/duplicate Champions held item/);
  const distinct=[{...p1[0],item:'Leftovers'},{...p1[1],item:'Sitrus Berry'},...p1.slice(2)];
  assert.doesNotThrow(()=>validateTeamConstraints(distinct,dex,allowed));
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
