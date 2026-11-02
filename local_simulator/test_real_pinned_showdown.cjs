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

function fixture({p2Mega=false}={}) {
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
  if(p2Mega){
    // Source-backed pinned-Showdown fixture only. Garchompite exists in its
    // item data and Champions mod returns the item-defined Mega form.
    p2[0]={name:"P2 Garchomp",species:"Garchomp",ability:"Rough Skin",
      item:"Garchompite",moves:["dragonclaw","earthquake","protect","swordsdance"],
      nature:"Jolly",level:50};
  }
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
  const restored=clone(original);
  const details={
    battleState:restored.requestState,
    sideState:restored.p2.requestState,
    request:restored.p2.activeRequest,
    activeHp:restored.p2.active[0].hp,
    activeFainted:restored.p2.active[0].fainted,
    activeSwitchFlag:restored.p2.active[0].switchFlag,
    choice:restored.p2.choice,
    pokemonLeft:restored.p2.pokemonLeft,
  };
  const result=enumerateEngineP2(Battle,original.toJSON());
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",
    result.reason+"; restored="+JSON.stringify(details));
  assert.deepEqual(result.commands,["switch 2","switch 3"]);
  assert.equal(result.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: transient request edits cannot forge saved Battle policy",()=>{
  const b=fixture();
  const immutableBefore=b.toJSON();
  // The pinned serializer deliberately omits activeRequest and regenerates it
  // in Battle.fromJSON. This edit is an in-memory test, not fixture evidence.
  b.p2.activeRequest.active[0].canMegaEvo=true;
  assert.deepEqual(b.toJSON(),immutableBefore);
  const result=enumerateEngineP2(Battle,immutableBefore);
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",result.reason);
  assert.equal(result.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: no runtime or wrong format yields NO_CHOICES",()=>{
  assert.equal(enumerateEngineP2(null,{}).status,"NO_CHOICES");
  const b=fixture();
  const invalid={...b.toJSON(),requestState:"teampreview"};
  assert.equal(enumerateEngineP2(Battle,invalid).status,"NO_CHOICES");
});

test("real pinned engine: single Mega produces distinct exact cloned choices",()=>{
  const original=fixture({p2Mega:true});
  const raw=original.toJSON();
  const request=original.p2.activeRequest;
  assert.equal(request.active[0].canMegaEvo,true,
    "Pinned Champions Showdown must issue a real Mega request");
  assert.ok(original.p2.active[0].canMegaEvo);
  const before=fingerprint(raw);
  const result=enumerateEngineP2(Battle,raw);
  assert.equal(result.status,"ENGINE_CHECKED_SIMPLE_CHOICES",result.reason);
  assert.equal(result.scope,"PINNED_SHOWDOWN_SINGLE_MEGA_SINGLES_ONLY");
  assert.equal(result.exhaustive_legal_choices_verified,false);
  assert.ok(result.commands.includes("move 1"));
  assert.ok(result.commands.includes("move 1 mega"));
  assert.ok(result.commands.includes("switch 2"));
  assert.equal(result.commands.filter(c=>c.endsWith(" mega")).length,4);
  for(const cmd of result.commands){
    const clone=Battle.fromJSON(raw);
    assert.equal(clone.p2.choose(cmd),true,cmd);
    const action=clone.p2.choice.actions[0];
    if(cmd.endsWith(" mega")){
      assert.equal(action.mega,true,cmd);
      assert.equal(action.moveid,request.active[0].moves[Number(cmd.split(" ")[1])-1].id);
    }else if(cmd.startsWith("move ")){
      assert.equal(!!action.mega,false,cmd);
    }
  }
  assert.equal(fingerprint(raw),before,"Source Battle/RNG must remain unchanged");
});
