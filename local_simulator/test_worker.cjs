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
