"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const path = require("node:path");

function run(lines) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(__dirname, "worker.cjs")], {
      env: {...process.env, SHOWDOWN_ROOT: "", SHOWDOWN_EXPECTED_SHA: ""},
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "", stderr = "";
    child.stdout.on("data", x => stdout += x);
    child.stderr.on("data", x => stderr += x);
    child.on("error", reject);
    child.on("exit", code => {
      if (code !== 0) return reject(new Error("worker exit "+code+" "+stderr));
      try { resolve(stdout.trim().split("\n").map(x => JSON.parse(x))); }
      catch (e) { reject(e); }
    });
    child.stdin.end(lines.map(x => JSON.stringify(x)).join("\n")+"\n");
  });
}
const base = {protocol:"issue140.v1",op:"HELLO",request_id:"r1",identity:{},payload:{}};
test("unbundled worker reports UNSUPPORTED without fabricating engine", async () => {
  const out = await run([base]);
  assert.equal(out.length, 1);
  assert.equal(out[0].capability_status, "UNSUPPORTED");
  assert.equal(out[0].request_id, "r1");
  assert.equal(out[0].ok, false);
});
test("unsupported partial observations do not become synthetic simulation", async () => {
  const q = {...base, op:"SIMULATE",payload:{source:"PARTIAL_OBSERVED"}};
  const out = await run([q]);
  assert.equal(out[0].capability_status, "UNSUPPORTED");
  assert.notEqual(out[0].capability_status, "CONFIRMED");
});
test("unknown protocol fails closed", async () => {
  const out = await run([{...base,protocol:"bad"}]);
  assert.equal(out[0].capability_status, "UNKNOWN");
});


test("staged fake engine enforces branch hash and echoes exact independent branch IDs", async () => {
  // Fake engine is for wire validation ONLY: not Pokémon mechanics or game evidence.
  const fs=require("node:fs"),os=require("node:os"),crypto=require("node:crypto");
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),"phase1-test-staged-"));
  const workerPath=path.join(__dirname,"worker.cjs");
  const PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
  const sha=file=>crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  const stable=value=>{
    if(Array.isArray(value))return "["+value.map(stable).join(",")+"]";
    if(value&&typeof value==="object")return "{"+
      Object.keys(value).sort().map(k=>JSON.stringify(k)+":"+stable(value[k])).join(",")+"}";
    return JSON.stringify(value);
  };
  const hash=value=>crypto.createHash("sha256").update(stable(value)).digest("hex");
  try{
    const root=path.join(tmp,"showdown");
    fs.mkdirSync(path.join(root,"dist","sim"),{recursive:true});
    fs.writeFileSync(path.join(root,".champions-source-sha"),PIN+"\n");
    fs.writeFileSync(path.join(root,"dist","sim","index.js"),
      "exports.Battle={fromJSON(data){const pokemon=data.team.map(x=>({"+
      "species:{id:x.species_id},moveSlots:x.moves.map(id=>({id})),hp:100,fainted:false,status:''}));"+
      "const p1={pokemon,active:[pokemon[data.activeIndex ?? 0]]};"+
      "return {format:{mod:'champions'},requestState:'move',turn:1,"+
      "field:{weather:''},sides:[p1,{pokemon:[]}],"+
      "makeChoices(){this.turn++},toJSON(){return data}}}};");
    fs.mkdirSync(path.join(root,"node_modules","testdep"),{recursive:true});
    fs.writeFileSync(path.join(root,"node_modules","testdep","index.js"),"// verified-test-dependency");
    fs.writeFileSync(path.join(root,"dist","sim","helper.js"),"// verified-test-helper");
    fs.writeFileSync(path.join(root,"package.json"),"{}");
    fs.writeFileSync(path.join(root,"LICENSE"),"test-only");
    const engineFileSha={
      "dist/sim/index.js":sha(path.join(root,"dist","sim","index.js")),
      "dist/sim/helper.js":sha(path.join(root,"dist","sim","helper.js")),
      "node_modules/testdep/index.js":sha(path.join(root,"node_modules","testdep","index.js")),
      "package.json":sha(path.join(root,"package.json")),
      "LICENSE":sha(path.join(root,"LICENSE"))
    };
    fs.writeFileSync(path.join(tmp,"NODE_LICENSE.txt"),"test-only");
    fs.writeFileSync(path.join(tmp,"THIRD_PARTY_NOTICES.txt"),"test-only");
    const manifest={scope:"ISSUE_140_SYNTHETIC_ONLY",engine_sha:PIN,
      production_enabled:false,
      showdown_entry_sha256:sha(path.join(root,"dist","sim","index.js")),
      engine_file_sha256:engineFileSha,
      worker_sha256:sha(workerPath),node_exe_sha256:sha(process.execPath),
      node_license_sha256:sha(path.join(tmp,"NODE_LICENSE.txt")),
      third_party_notices_sha256:sha(path.join(tmp,"THIRD_PARTY_NOTICES.txt"))};
    fs.writeFileSync(path.join(tmp,"manifest.json"),JSON.stringify(manifest));
    const baseIdentity={match_id:"synthetic",decision_window_id:"one",
      team_sha256:"team",simulator_sha:PIN,allowed_action_digest:"digest"};
    const roster=[
      {name:"Garchomp",species_id:"garchomp",moves:["earthquake","dragonclaw"]},
      {name:"Primarina",species_id:"primarina",moves:["moonblast"]},
      {name:"Mimikyu",species_id:"mimikyu",moves:["playrough"]}
    ];
    const payload={source:"FULL_REPLAY_SYNTHETIC",
      battle:{team:roster.map(x=>({species_id:x.species_id,moves:[...x.moves]})),
        activeIndex:0},
      expected_p1_roster:roster,expected_p1_active:"Garchomp",
      p1_choice:"move 1",p2_choice:"move 1",hypothesis_id:"H1",
      candidate_action_id:"a".repeat(64)};
    payload.branch_input_sha256=hash(payload);
    const req={protocol:"issue140.v1",op:"SIMULATE",request_id:"same-window",
      snapshot_sha256:"same-snapshot",branch_request_id:"b".repeat(32),
      identity:baseIdentity,payload};
    const wrong={...req,branch_request_id:"c".repeat(32),payload:{
      ...payload,p2_choice:"move 2"}};
    const reordered={...payload,battle:{team:[
      payload.battle.team[0],payload.battle.team[2],payload.battle.team[1]],activeIndex:0}};
    reordered.branch_input_sha256=hash(Object.fromEntries(
      Object.entries(reordered).filter(([key])=>key!=="branch_input_sha256")));
    const mismatchedParty={...req,branch_request_id:"d".repeat(32),payload:reordered};
    const wrongMoves={...payload,battle:{team:payload.battle.team.map((x,i)=>({
      ...x,moves:i===0?["dragonclaw","earthquake"]:x.moves}))}};
    wrongMoves.branch_input_sha256=hash(Object.fromEntries(
      Object.entries(wrongMoves).filter(([key])=>key!=="branch_input_sha256")));
    const mismatchedMoves={...req,branch_request_id:"e".repeat(32),payload:wrongMoves};
    const wrongActive={...payload,battle:{...payload.battle,activeIndex:1}};
    wrongActive.branch_input_sha256=hash(Object.fromEntries(
      Object.entries(wrongActive).filter(([key])=>key!=="branch_input_sha256")));
    const mismatchedActive={...req,branch_request_id:"f".repeat(32),payload:wrongActive};
    // The nonce and branch hash are valid for the wrong roster/moves,
    // so only semantic inspection after Battle.fromJSON can reject these.
    // Mutated choice is NOT covered by the submitted SHA.
    const child=spawn(process.execPath,[workerPath],{
      env:{...process.env,SHOWDOWN_ROOT:root,SHOWDOWN_EXPECTED_SHA:PIN},
      stdio:["pipe","pipe","pipe"]});
    const output=await new Promise((resolve,reject)=>{
      let data="",errors="";
      child.stdout.on("data",chunk=>data+=chunk);
      child.stderr.on("data",chunk=>errors+=chunk);
      child.on("error",reject);
      child.on("exit",code=>{
        if(code!==0)return reject(new Error("worker exited "+code+" "+errors));
        try{resolve(data.trim().split("\n").map(JSON.parse))}catch(e){reject(e)}
      });
      child.stdin.end([req,wrong,mismatchedParty,mismatchedMoves,mismatchedActive].map(JSON.stringify).join("\n")+"\n");
    });
    assert.equal(output.length,5);
    assert.equal(output[0].capability_status,"PARTIAL");
    assert.equal(output[0].ok,true);
    assert.equal(output[0].branch_request_id,"b".repeat(32));
    assert.equal(output[0].hypothesis_id,"H1");
    assert.equal(output[0].branch_input_sha256,payload.branch_input_sha256);
    assert.equal(output[0].candidate_action_id,"a".repeat(64));
    assert.equal(output[1].capability_status,"UNSUPPORTED");
    assert.equal(output[1].ok,false);
    assert.equal(output[1].branch_request_id,"c".repeat(32));
    for(const index of [2,3,4]) {
      assert.equal(output[index].ok,false);
      assert.equal(output[index].capability_status,"UNSUPPORTED");
      assert.equal(output[index].reason,"P1_ROSTER_OR_MOVE_SLOT_MISMATCH");
    }
    // Manifest entrypoint and pinned source SHA remain unchanged, but a
    // secondary runtime JS dependency has been changed after staging.
    fs.writeFileSync(path.join(root,"node_modules","testdep","index.js"),
                     "// malicious or accidental dependency modification");
    const corrupt=await new Promise((resolve,reject)=>{
      const proc=spawn(process.execPath,[workerPath],{
        env:{...process.env,SHOWDOWN_ROOT:root,SHOWDOWN_EXPECTED_SHA:PIN},
        stdio:["pipe","pipe","pipe"]
      });
      let stdout="",stderr="";
      proc.stdout.on("data",x=>stdout+=x);
      proc.stderr.on("data",x=>stderr+=x);
      proc.on("error",reject);
      proc.on("exit",code=>{
        if(code!==0)return reject(new Error("worker exit "+code+" "+stderr));
        try{resolve(JSON.parse(stdout.trim()))}catch(e){reject(e)}
      });
      proc.stdin.end(JSON.stringify(base)+"\n");
    });
    assert.equal(corrupt.capability_status,"UNSUPPORTED");
    assert.equal(corrupt.reason,"RUNTIME_MANIFEST_SHA_MISMATCH");
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
