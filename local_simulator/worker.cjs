"use strict";

// Issue #140: read-only, opt-in JSONL worker. NO strategy, GUI or SQLite IO.
// The upstream engine is usable only after #139 validates its real-game scope.
// Never report CONFIRMED from synthetic Showdown execution.
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const crypto = require("node:crypto");
const {enumerateEngineP2} = require("./p2_engine_choices.cjs");

const VERSION = "issue140.v1";
const PIN = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const MAX = 1024 * 1024;
let Battle = null, unavailable = "ENGINE_NOT_BUNDLED_OR_UNVERIFIED";

function fileHash(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}
function runtimeFileInventory(root) {
  const inventory={};
  function visit(dir, relative="") {
    const children=fs.readdirSync(dir,{withFileTypes:true});
    for(const child of children) {
      const name=relative ? relative+"/"+child.name : child.name;
      const absolute=path.join(dir,child.name);
      if(child.isSymbolicLink())throw Error("runtime tree symlink");
      if(child.isDirectory())visit(absolute,name);
      else if(child.isFile() && name!==".champions-source-sha")
        inventory[name]=fileHash(absolute);
      else if(!child.isFile())throw Error("runtime tree special file");
    }
  }
  visit(root);
  return inventory;
}
function matchesVerifiedInventory(root,files) {
  if(!files || typeof files!=="object" || Array.isArray(files) ||
      Object.keys(files).length===0)return false;
  const actual=runtimeFileInventory(root);
  const keys=Object.keys(actual).sort();
  const expected=Object.keys(files).sort();
  return keys.length===expected.length &&
    keys.every((k,i)=>k===expected[i] && actual[k]===files[k]);
}
function tryLoadEngine() {
  const root = process.env.SHOWDOWN_ROOT || "";
  const expected = process.env.SHOWDOWN_EXPECTED_SHA || "";
  // This marker is produced only by the explicit offline staging step.
  const marker = path.join(root, ".champions-source-sha");
  const file = path.join(root, "dist", "sim", "index.js");
  const packageRoot = path.dirname(root);
  const manifestPath = path.join(packageRoot, "manifest.json");
  const noticePath = path.join(packageRoot, "THIRD_PARTY_NOTICES.txt");
  const nodeLicensePath = path.join(packageRoot, "NODE_LICENSE.txt");
  if (expected !== PIN || !root || !fs.existsSync(marker) ||
      !fs.existsSync(file) || !fs.existsSync(manifestPath) ||
      !fs.existsSync(noticePath) || !fs.existsSync(nodeLicensePath)) return;
  if (fs.readFileSync(marker, "utf8").trim() !== PIN) return;
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    if (manifest.scope !== "ISSUE_140_SYNTHETIC_ONLY" ||
        manifest.engine_sha !== PIN || manifest.production_enabled !== false ||
        manifest.showdown_entry_sha256 !== fileHash(file) ||
        !matchesVerifiedInventory(root,manifest.engine_file_sha256) ||
        manifest.worker_sha256 !== fileHash(__filename) ||
        manifest.p2_engine_choices_sha256 !== fileHash(path.join(__dirname,"p2_engine_choices.cjs")) ||
        manifest.node_exe_sha256 !== fileHash(process.execPath) ||
        manifest.node_license_sha256 !== fileHash(nodeLicensePath) ||
        manifest.third_party_notices_sha256 !== fileHash(noticePath)) {
      unavailable = "RUNTIME_MANIFEST_SHA_MISMATCH";
      return;
    }
    ({Battle} = require(file));
    if (!Battle || typeof Battle.fromJSON !== "function")
      throw Error("no Battle.fromJSON");
    unavailable = "";
  } catch (err) {
    unavailable = "SHOWDOWN_LOAD_FAILED";
  }
}
tryLoadEngine();

function stable(value) {
  if (Array.isArray(value)) return "[" + value.map(stable).join(",") + "]";
  if (value && typeof value === "object") return "{" +
    Object.keys(value).sort().map(k => JSON.stringify(k)+":"+stable(value[k])).join(",") + "}";
  return JSON.stringify(value);
}
function fingerprint(obj) {
  return crypto.createHash("sha256").update(stable(obj)).digest("hex");
}
function reply(req, capability_status, reason, payload={}, ok=false) {
  const out = {
    protocol: VERSION, op: req && req.op, request_id: req && req.request_id,
    identity: req && req.identity,
    snapshot_sha256: req && req.snapshot_sha256,
    branch_request_id: req && req.branch_request_id,
    hypothesis_id: req && req.payload && req.payload.hypothesis_id,
    branch_input_sha256: req && req.payload && req.payload.branch_input_sha256,
    candidate_action_id: req && req.payload && req.payload.candidate_action_id,
    capability_status, reason, payload, ok
  };
  process.stdout.write(JSON.stringify(out) + "\n");
}
function summary(b) {
  return {
    turn: b.turn,
    request_state: b.requestState,
    weather: b.field.weather,
    side_hp: b.sides.map(s => s.pokemon.map(p => ({
      species: p.species.id, hp: p.hp, fainted: p.fainted,
      status: p.status
    })))
  };
}
function matchesP1BattleRoster(b, expected, activeName) {
  // Check the exact simulator instances *after* Battle.fromJSON, before
  // makeChoices. A Python-side slot number is not evidence of Battle order.
  if (!Array.isArray(expected) || expected.length !== 3 ||
      !expected.every(x=>x && typeof x.name==="string" && x.name &&
        typeof x.species_id==="string" && x.species_id &&
        Array.isArray(x.moves) && x.moves.length>0 &&
        x.moves.every(m=>typeof m==="string" && !!m)) ||
      !b || !Array.isArray(b.sides) || !b.sides[0] ||
      !Array.isArray(b.sides[0].pokemon) ||
      b.sides[0].pokemon.length !== expected.length) return false;
  const side=b.sides[0];
  for (let i=0; i<expected.length; i++) {
    const p=side.pokemon[i], e=expected[i];
    if (!p || !p.species || p.species.id !== e.species_id ||
        !Array.isArray(p.moveSlots) ||
        p.moveSlots.length !== e.moves.length ||
        !p.moveSlots.every((m,j)=>m && m.id===e.moves[j])) return false;
  }
  if (typeof activeName!=="string") return false;
  if (activeName) {
    const ix=expected.findIndex(x=>x.name===activeName);
    if (ix<0 || !Array.isArray(side.active) || !side.active[0] ||
        side.active[0]!==side.pokemon[ix]) return false;
  }
  return true;
}

function handle(req) {
  if (!req || typeof req !== "object" || req.protocol !== VERSION ||
      typeof req.request_id !== "string" || typeof req.op !== "string" ||
      !req.identity || typeof req.identity !== "object") {
    reply(req || {}, "UNKNOWN", "INVALID_PROTOCOL");
    return;
  }
  if (req.op === "HELLO") {
    reply(req, Battle ? "PARTIAL" : "UNSUPPORTED",
          Battle ? "SYNTHETIC_ONLY_NOT_CHAMPIONS_VERIFIED" : unavailable,
          {engine_sha: PIN}, !!Battle);
    return;
  }
  if (!["SIMULATE","ENUMERATE_P2"].includes(req.op)) {
    reply(req, "UNSUPPORTED", "UNKNOWN_OPERATION");
    return;
  }
  if (!Battle) {
    reply(req, "UNSUPPORTED", unavailable);
    return;
  }
  const data = req.payload || {};
  if (req.op === "ENUMERATE_P2") {
    // A separate read-only protocol operation. It returns only options
    // checked on independent engine clones, not LIVE tactical advice.
    if (data.source !== "FULL_REPLAY_SYNTHETIC" ||
        typeof req.branch_request_id !== "string" ||
        !/^[a-f0-9]{32}$/.test(req.branch_request_id) ||
        typeof data.hypothesis_id !== "string" ||
        typeof data.branch_input_sha256 !== "string" ||
        !/^[a-f0-9]{64}$/.test(data.branch_input_sha256) ||
        data.branch_input_sha256 !== fingerprint(Object.fromEntries(
          Object.entries(data).filter(([k]) => k !== "branch_input_sha256"))) ||
        !data.battle || typeof data.battle !== "object" ||
        Array.isArray(data.battle) ||
        !Array.isArray(data.expected_p1_roster) ||
        typeof data.expected_p1_active !== "string" ||
        !req.identity.match_id || !req.identity.decision_window_id ||
        !req.identity.team_sha256 || req.identity.simulator_sha !== PIN ||
        !req.identity.allowed_action_digest || !req.snapshot_sha256) {
      reply(req,"UNSUPPORTED","INCOMPLETE_OR_REAL_OBSERVATION");
      return;
    }
    try {
      const b=Battle.fromJSON(data.battle);
      if (!matchesP1BattleRoster(b,data.expected_p1_roster,data.expected_p1_active)) {
        reply(req,"UNSUPPORTED","P1_ROSTER_OR_MOVE_SLOT_MISMATCH");
        return;
      }
      const result=enumerateEngineP2(Battle,data.battle);
      if (result.status!=="ENGINE_CHECKED_SIMPLE_CHOICES") {
        reply(req,"UNSUPPORTED",result.reason);
        return;
      }
      reply(req,"PARTIAL","SYNTHETIC_P2_SIMPLE_ENGINE_CHECKED_NOT_CHAMPIONS_VERIFIED",{
        ...result,source:"FULL_REPLAY_SYNTHETIC",
        hypothesis_id:data.hypothesis_id,
        branch_input_sha256:data.branch_input_sha256
      },true);
    } catch (_err) {
      reply(req,"UNKNOWN","ENGINE_P2_UNAVAILABLE_OR_INVALID");
    }
    return;
  }
  if (data.source !== "FULL_REPLAY_SYNTHETIC" ||
      typeof req.branch_request_id !== "string" ||
      !/^[a-f0-9]{32}$/.test(req.branch_request_id) ||
      typeof data.hypothesis_id !== "string" ||
      typeof data.candidate_action_id !== "string" ||
      !/^[a-f0-9]{64}$/.test(data.candidate_action_id) ||
      !Array.isArray(data.expected_p1_roster) ||
      typeof data.expected_p1_active !== "string" ||
      typeof data.branch_input_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(data.branch_input_sha256) ||
      data.branch_input_sha256 !== fingerprint(Object.fromEntries(
        Object.entries(data).filter(([key]) => key !== "branch_input_sha256"))) ||
      !data.battle || typeof data.battle !== "object" ||
      typeof data.p1_choice !== "string" || !data.p1_choice ||
      typeof data.p2_choice !== "string" || !data.p2_choice ||
      !req.identity.match_id || !req.identity.decision_window_id ||
      !req.identity.team_sha256 || req.identity.simulator_sha !== PIN ||
      !req.identity.allowed_action_digest) {
    reply(req, "UNSUPPORTED", "INCOMPLETE_OR_REAL_OBSERVATION");
    return;
  }
  try {
    const b = Battle.fromJSON(data.battle);
    if (b.format.mod !== "champions") {
      reply(req, "UNSUPPORTED", "NOT_CHAMPIONS_MOD");
      return;
    }
    if (!["move", "switch", "teampreview"].includes(b.requestState)) {
      reply(req, "UNSUPPORTED", "UNKNOWN_DECISION_WINDOW");
      return;
    }
    if (!matchesP1BattleRoster(b,data.expected_p1_roster,data.expected_p1_active)) {
      reply(req, "UNSUPPORTED", "P1_ROSTER_OR_MOVE_SLOT_MISMATCH");
      return;
    }
    b.makeChoices(data.p1_choice, data.p2_choice);
    reply(req, "PARTIAL", "SYNTHETIC_SIMULATION_NOT_GAME_VERIFIED",
          {next: summary(b), simulated_state_sha256: fingerprint(b.toJSON())}, true);
  } catch (err) {
    reply(req, "UNKNOWN", "SIMULATION_FAILED");
  }
}

const reader = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
reader.on("line", line => {
  if (Buffer.byteLength(line, "utf8") > MAX) {
    reply({}, "UNSUPPORTED", "MESSAGE_TOO_LARGE");
    return;
  }
  let req;
  try { req = JSON.parse(line); } catch {
    reply({}, "UNKNOWN", "MALFORMED_JSON");
    return;
  }
  handle(req);
});
