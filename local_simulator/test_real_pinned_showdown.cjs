"use strict";
// Real pinned-engine integration test. This is NOT real Pokémon Champions data.
// Do not generate fixture evidence from another title or treat CI success as
// full game compatibility. Fail if no real pinned Showdown build is provided.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {enumerateEngineP2} = require("./p2_engine_choices.cjs");

const PIN = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const root = process.env.SHOWDOWN_PINNED_BUILD_ROOT;
assert.ok(root, "A real pinned Showdown build is REQUIRED; fake engine cannot substitute");
const marker = path.join(root, ".git", "HEAD");
assert.ok(fs.existsSync(marker), "Expected the checked-out upstream repository");
const entry = path.join(root, "dist", "sim", "index.js");
assert.ok(fs.existsSync(entry), "Expected the compiled pinned Showdown simulator");
const {Battle} = require(entry);
assert.equal(typeof Battle, "function", "Use real Battle class, not a mock");
assert.equal(typeof Battle.fromJSON, "function");

function fixture() {
  // Synthetic 3v3 teams. Their identity/sets are intentionally fully known;
  // never use these as Pokémon Champions usage-rate or ladder evidence.
  const p1=[
    {name:"P1 Garchomp",species:"Garchomp",ability:"Rough Skin",item:"Leftovers",
     moves:["earthquake","dragonclaw","protect","swordsdance"],nature:"Jolly",level:50},
    {name:"P1 Primarina",species:"Primarina",ability:"Torrent",item:"Sitrus Berry",
     moves:["moonblast","surf","protect","calmmind"],nature:"Modest",level:50},
    {name:"P1 Mimikyu",species:"Mimikyu",ability:"Disguise",item:"Life Orb",
     moves:["playrough","shadowclaw","protect","swordsdance"],nature:"Jolly",level:50},
  ];
  const p2=[
    {name:"P2 Dragonite",species:"Dragonite",ability:"Inner Focus",item:"Leftovers",
     moves:["dragonclaw","extremespeed","protect","roost"],nature:"Adamant",level:50},
    {name:"P2 Gholdengo",species:"Gholdengo",ability:"Good as Gold",item:"Leftovers",
     moves:["shadowball","makeitrain","protect","recover"],nature:"Modest",level:50},
    {name:"P2 Kingambit",species:"Kingambit",ability:"Defiant",item:"Black Glasses",
     moves:["kowtowcleave","suckerpunch","protect","swordsdance"],nature:"Adamant",level:50},
  ];
  const b=new Battle({
    formatid:"gen9championsbssregmc",
    seed:[123,456,789,321],
    p1:{name:"One",team:p1},p2:{name:"Two",team:p2},
    strictChoices:true,
  });
  assert.equal(b.format.mod,"champions");
  assert.equal(b.gameType,"singles");
  if(b.requestState==="teampreview"){
    assert.equal(b.choose("p1","team 1,2,3"),true);
    assert.equal(b.choose("p2","team 1,2,3"),true);
  }
  assert.equal(b.requestState,"move","Must be a real engine-issued move request");
  assert.equal(b.sides[1].activeRequest?.side?.id,"p2");
  assert.equal(b.sides[1].pokemon.length,3);
  return b;
}

function clone(b) {return Battle.fromJSON(b.toJSON());}
function fingerprint(x) {return crypto.createHash("sha256").update(JSON.stringify(x)).digest("hex");}

test("real pinned engine: complete synthetic 3v3 Battle preserves P2 request and RNG",()=>{
  const original=fixture();
  const restored=clone(original);
  assert.equal(restored.requestState,original.requestState);
  assert.equal(restored.p2.activeRequest?.side?.id,"p2");
  assert.deepEqual(restored.toJSON(),original.toJSON(),"Full Battle serialization roundtrip");
  assert.deepEqual(restored.prng.seed,original.prng.seed);
  assert.equal(restored.format.mod,"champions");
});

test("real pinned engine: simple P2 choices accepted on independent copies",()=>{
  const original=fixture();
  const raw=original.toJSON();
  const before=fingerprint(raw);
  const result=enumerateEngineP2(Battle,raw);
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",result.reason);
  assert.ok(result.commands.includes("move 1"));
  assert.ok(result.commands.includes("switch 2"));
  assert.ok(result.commands.includes("switch 3"));
  assert.equal(result.exhaustive_legal_choices_verified,false);
  assert.equal(result.scope,"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY");
  for(const cmd of result.commands) {
    const next=Battle.fromJSON(raw);
    assert.equal(next.p2.choose(cmd),true,`Engine rejected ${cmd}`);
    assert.equal(next.p2.isChoiceDone(),true,`Incomplete ${cmd}`);
    assert.equal(next.p2.choice.error,"");
  }
  assert.equal(fingerprint(raw),before,"Enumeration must never change source Battle");
});

test("real pinned engine: forced replacement from synthetic engine request",()=>{
  const original=fixture();
  const target=original.p2.active[0];
  // Synthetic boundary setup: do NOT claim this was a natural-game KO.
  // The real engine generates a forced-switch ChoiceRequest from this state.
  target.hp=0;
  target.fainted=true;
  target.faintQueued=false;
  target.switchFlag=true;
  original.p2.pokemonLeft=2;
  original.makeRequest("switch");
  assert.equal(original.p2.requestState,"switch");
  assert.deepEqual(original.p2.activeRequest.forceSwitch,[true]);
  const result=enumerateEngineP2(Battle,original.toJSON());
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",result.reason);
  assert.deepEqual(result.commands,["switch 2","switch 3"]);
  assert.equal(result.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: tampered request is not treated as exhaustive legality",()=>{
  const b=fixture();
  b.p2.activeRequest.active[0].canMegaEvo=true;
  const result=enumerateEngineP2(Battle,b.toJSON());
  assert.equal(result.status,"NO_CHOICES");
  assert.deepEqual(result.commands,[]);
  assert.equal(result.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: no runtime or wrong format yields NO_CHOICES",()=>{
  assert.equal(enumerateEngineP2(null,{}).status,"NO_CHOICES");
  const b=fixture();
  const invalid={...b.toJSON(),requestState:"teampreview"};
  assert.equal(enumerateEngineP2(Battle,invalid).status,"NO_CHOICES");
});
