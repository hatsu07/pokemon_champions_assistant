"use strict";

// Issue #140: read-only, opt-in JSONL worker. NO strategy, GUI or SQLite IO.
// The upstream engine is usable only after #139 validates its real-game scope.
// Never report CONFIRMED from synthetic Showdown execution.
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const crypto = require("node:crypto");

const VERSION = "issue140.v1";
const PIN = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1";
const MAX = 1024 * 1024;
let Battle = null, unavailable = "ENGINE_NOT_BUNDLED_OR_UNVERIFIED";

function tryLoadEngine() {
  const root = process.env.SHOWDOWN_ROOT || "";
  const expected = process.env.SHOWDOWN_EXPECTED_SHA || "";
  // This marker is produced only by the explicit offline staging step.
  const marker = path.join(root, ".champions-source-sha");
  const file = path.join(root, "dist", "sim", "index.js");
  if (expected !== PIN || !root || !fs.existsSync(marker) || !fs.existsSync(file))
    return;
  if (fs.readFileSync(marker, "utf8").trim() !== PIN) return;
  try {
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
  if (req.op !== "SIMULATE") {
    reply(req, "UNSUPPORTED", "UNKNOWN_OPERATION");
    return;
  }
  if (!Battle) {
    reply(req, "UNSUPPORTED", unavailable);
    return;
  }
  const data = req.payload || {};
  if (data.source !== "FULL_REPLAY_SYNTHETIC" ||
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
