'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {calculate,halfDownRatio}=require('./wiki_damage_reference.cjs');
const {cases, sources, schema}=JSON.parse(fs.readFileSync(path.join(__dirname,'wiki_damage_vectors.json'),'utf8'));
const neutral={attack:182,defense:150,basePower:100,powerMultiplier:[1,1],weatherMultiplier:[1,1],critical:false,stabMultiplier:[3,2],typeMultiplier:[2,1],otherMultiplier:[1,1]};
test('reference data has source URLs and independent wiki expectations',()=>{
  assert.equal(schema,'champions-wiki-reference-v1');
  for(const c of cases){assert.ok(c.source_ids.length);for(const id of c.source_ids)assert.ok(sources[id]?.url?.startsWith('https://'));}
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
