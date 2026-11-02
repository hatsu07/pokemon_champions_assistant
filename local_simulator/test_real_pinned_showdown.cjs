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
const {auditSyntheticObservedReplay,SOURCE,sha256} =
  require("./observed_replay_audit.cjs");

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

function fixture({p2Mega=false,p2Choice=false,p2Outrage=false}={}) {
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
  if(p2Choice) p2[0].item="Choice Band";
  if(p2Outrage) p2[0].moves[0]="outrage";
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

test("real pinned engine: one exhausted PP slot is excluded, with other choices retained",()=>{
  const b=fixture();
  b.p2.active[0].moveSlots[0].pp=0;
  b.makeRequest("move");
  const req=b.p2.activeRequest.active[0];
  assert.equal(req.moves.length,4);
  assert.equal(req.moves[0].disabled,true);
  const r=enumerateEngineP2(Battle,b.toJSON());
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.equal(r.commands.includes("move 1"),false);
  assert.ok(r.commands.includes("move 2"));
  assert.ok(r.commands.includes("switch 2"));
  assert.equal(r.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: all PP zero yields exactly Struggle and normal switch",()=>{
  const b=fixture();
  for(const slot of b.p2.active[0].moveSlots)slot.pp=0;
  b.makeRequest("move");
  const req=b.p2.activeRequest.active[0];
  assert.deepEqual(req.moves.map(m=>m.id),["struggle"]);
  assert.equal(req.moves[0].pp,undefined);
  const raw=b.toJSON(),before=fingerprint(raw);
  const r=enumerateEngineP2(Battle,raw);
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.equal(r.scope,"PINNED_SHOWDOWN_HARDLOCK_OR_STRUGGLE_SINGLES_ONLY");
  assert.deepEqual(r.commands,["move 1","switch 2","switch 3"]);
  const separate=Battle.fromJSON(raw);
  assert.equal(separate.p2.choose("move 1"),true);
  assert.equal(separate.p2.choice.actions[0].moveid,"struggle");
  assert.equal(separate.p2.choice.actions[0].moveSlot,undefined);
  assert.equal(fingerprint(raw),before);
  assert.equal(r.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: hard locked Outrage has only a slot-less forced move",()=>{
  // Synthetic effect injection (not a claimed naturally observed game turn).
  const b=fixture({p2Outrage:true});
  const p=b.p2.active[0];
  assert.equal(p.addVolatile("lockedmove",p,b.dex.moves.get("outrage")),true);
  assert.equal(p.getLockedMove(),"outrage");
  b.makeRequest("move");
  const req=b.p2.activeRequest.active[0];
  assert.deepEqual(req.moves.map(m=>m.id),["outrage"]);
  assert.equal(req.moves[0].pp,undefined);
  assert.equal(req.trapped,true);
  const raw=b.toJSON(),before=fingerprint(raw);
  const r=enumerateEngineP2(Battle,raw);
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.equal(r.scope,"PINNED_SHOWDOWN_HARDLOCK_OR_STRUGGLE_SINGLES_ONLY");
  assert.deepEqual(r.commands,["move 1"]);
  const separate=Battle.fromJSON(raw);
  assert.equal(separate.p2.choose("move 1"),true);
  assert.equal(separate.p2.choice.actions[0].moveid,"outrage");
  assert.equal(separate.p2.choice.actions[0].moveSlot,undefined);
  assert.equal(fingerprint(raw),before);
  assert.equal(r.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: Choice Band restricts moves but allows bench switches",()=>{
  const b=fixture({p2Choice:true});
  b.makeChoices("move 3","move 1");
  assert.equal(b.requestState,"move");
  assert.ok(b.p2.active[0].volatiles.choicelock);
  const req=b.p2.activeRequest.active[0];
  assert.equal(req.moves.length,4);
  assert.equal(req.moves[0].disabled,false);
  assert.ok(req.moves.slice(1).every(m=>m.disabled===true));
  const r=enumerateEngineP2(Battle,b.toJSON());
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.deepEqual(r.commands,["move 1","switch 2","switch 3"]);
  assert.equal(r.scope,"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY");
  assert.equal(r.exhaustive_legal_choices_verified,false);
});

test("real pinned engine: synthetically applied Encore only offers enabled move",()=>{
  const b=fixture();
  const p=b.p2.active[0];
  // Not a cartridge observation; exercise actual pinned Encore condition
  // with a deterministically supplied previous move, not an inferred set.
  p.lastMove=b.dex.moves.get("protect");
  assert.equal(p.addVolatile("encore",b.p1.active[0],b.dex.moves.get("encore")),true);
  // In an actual nextTurn(), Battle runs DisableMove before issuing requests.
  // makeRequest() alone does not run that event, so reproduce that exact
  // event instead of treating a bare Encore volatile as a complete turn.
  b.runEvent("DisableMove",p);
  b.makeRequest("move");
  const req=b.p2.activeRequest.active[0];
  assert.equal(req.moves.length,4);
  assert.equal(req.moves[2].disabled,false);
  assert.ok(req.moves.filter((m,i)=>i!==2).every(m=>m.disabled===true));
  const r=enumerateEngineP2(Battle,b.toJSON());
  assert.equal(r.status,"ENGINE_CHECKED_SIMPLE_CHOICES",r.reason);
  assert.deepEqual(r.commands,["move 3","switch 2","switch 3"]);
  assert.equal(r.scope,"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY");
  assert.equal(r.exhaustive_legal_choices_verified,false);
});

function syntheticProjection(b) {
  const raw=b.toJSON();
  const one=(id,side)=>({
    side:id,slot:1,species_id:side.pokemon[0].species.id,
    hp:side.pokemon[0].hp,maxhp:side.pokemon[0].maxhp,
    fainted:side.pokemon[0].fainted,status:side.pokemon[0].status,
    boosts:{atk:side.pokemon[0].boosts.atk}
  });
  return {
    source:SOURCE,
    match_id:"controlled-real-engine-battle",
    decision_window_id:"synthetic-window-1",
    state_revision:1,battle_sha256:sha256(raw),
    active:{p1:1,p2:1},turn:b.turn,
    members:[one("p1",b.p1),one("p2",b.p2)],
    field:{weather:b.field.weather,terrain:b.field.terrain}
  };
}

test("real pinned engine: synthetic visible-state projection matches full clone, never restores hidden state",()=>{
  const b=fixture();
  const original=b.toJSON(),hash=fingerprint(original);
  const packet=syntheticProjection(b);
  const result=auditSyntheticObservedReplay(Battle,original,packet);
  assert.equal(result.status,"SYNTHETIC_VISIBLE_FIELDS_MATCH",result.reason);
  assert.equal(result.battle_sha256,packet.battle_sha256);
  assert.ok(result.checked_fields.includes("p2:1.hp"));
  assert.ok(result.checked_fields.includes("p2:1.boosts.atk"));
  assert.equal(result.full_state_reconstructed,false);
  assert.equal(result.exhaustive_observation_verified,false);
  assert.deepEqual(b.toJSON(),original,"No mutation of original Battle");
  assert.equal(fingerprint(original),hash,"No mutation of input JSON");
});
test("real pinned engine: mismatch of observed HP/active/boost/status fails",()=>{
  const b=fixture(),raw=b.toJSON();
  const mutate=[
    p=>p.members[1].hp-=1,
    p=>p.members[0].boosts.atk+=1,
    p=>p.members[0].status="brn",
    p=>p.active.p2=2
  ];
  for(const change of mutate) {
    const packet=syntheticProjection(b);
    change(packet);
    const r=auditSyntheticObservedReplay(Battle,raw,packet);
    assert.equal(r.status,"NO_OBSERVATION_MATCH");
    assert.equal(r.reason,"VISIBLE_STATE_MISMATCH",r.reason);
    assert.equal(r.full_state_reconstructed,false);
  }
});
test("real pinned engine: same visible observations do not determine RNG",()=>{
  const a=fixture(),rawA=a.toJSON();
  const packetA=syntheticProjection(a);
  const alt=clone(a);
  const originalSeed=alt.prng.seed.slice();
  alt.prng.seed[0]=(alt.prng.seed[0]+1) % 65536;
  assert.notDeepEqual(alt.prng.seed,originalSeed);
  const rawB=alt.toJSON();
  assert.notEqual(sha256(rawA),sha256(rawB),
    "Hidden RNG fork should have a different full serialized state");
  // The synthetic observation packet carries the same visible fields,
  // but points to whichever complete hypothetical Battle is being audited.
  const packetB={...packetA,battle_sha256:sha256(rawB)};
  assert.equal(
    auditSyntheticObservedReplay(Battle,rawA,packetA).status,
    "SYNTHETIC_VISIBLE_FIELDS_MATCH");
  const other=auditSyntheticObservedReplay(Battle,rawB,packetB);
  assert.equal(other.status,"SYNTHETIC_VISIBLE_FIELDS_MATCH",other.reason);
  assert.equal(other.full_state_reconstructed,false);
  assert.equal(
    auditSyntheticObservedReplay(Battle,rawB,packetA).reason,
    "REPLAY_SHA_MISMATCH",
    "An unchanged identity/hash must not be applied to a different replay");
});
