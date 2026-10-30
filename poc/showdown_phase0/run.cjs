"use strict";

const {spawn} = require("node:child_process");
const {resolve} = require("node:path");
const fs = require("node:fs");
const {performance} = require("node:perf_hooks");
const {assess} = require("./decision.cjs");

function connectWorker() {
  const worker = spawn(process.execPath, [resolve(__dirname, "worker.cjs")], {
    stdio: ["pipe", "pipe", "pipe"], env: process.env});
  const pending = new Map();
  let buffer = "", seq = 0;
  function rejectAll(err) {
    for (const task of pending.values()) { clearTimeout(task.timeout); task.reject(err); }
    pending.clear();
  }
  worker.stdout.setEncoding("utf8");
  worker.stdout.on("data", chunk => {
    buffer += chunk;
    while (buffer.includes("\n")) {
      const index = buffer.indexOf("\n");
      const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
      let result;
      try { result = JSON.parse(line); } catch { rejectAll(Error("invalid worker JSONL")); worker.kill(); return; }
      const task = pending.get(result.id);
      if (!task) continue; // stale/duplicate responses never accepted.
      clearTimeout(task.timeout); pending.delete(result.id); task.resolve(result);
    }
  });
  worker.stderr.on("data", () => {});
  worker.on("error", rejectAll);
  worker.on("exit", (code, signal) => rejectAll(Error("worker exited (" + code + ", " + signal + ")")));
  function request(method, count, timeoutMs) {
    const id = ++seq;
    return new Promise((resolveRequest, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(id); worker.kill();
        reject(new Error("worker request " + id + " timed out"));
      }, timeoutMs);
      pending.set(id, {resolve: resolveRequest, reject, timeout});
      worker.stdin.write(JSON.stringify({id, method, count}) + "\n");
    });
  }
  return {request, stop() { worker.kill(); }};
}
async function run() {
  const startedAt = new Date().toISOString();
  const worker = connectWorker();
  let probe = null, benchmark = null, error = null, startupMs = null;
  try {
    const start = performance.now();
    probe = await worker.request("probe", undefined, 30000);
    startupMs = performance.now() - start;
    if (probe.status !== "OK") throw new Error("probe failed: " + (probe.reason || "unknown"));
    benchmark = await worker.request("benchmark", 120, 120000);
    if (benchmark.status !== "OK") throw new Error("benchmark failed: " + (benchmark.reason || "unknown"));
  } catch (e) { error = String(e.message); }
  finally { worker.stop(); }
  const decision = assess(probe, benchmark);
  const result = {
    schema: "phase0-poc-v2", timestamp_utc: startedAt,
    upstream: "smogon/pokemon-showdown", pinned_sha: "ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
    target: "[Gen 9 Champions] BSS Reg M-C",
    context: {os: process.platform, arch: process.arch, node: process.version},
    startup_and_probe_ms: startupMs,
    probe, benchmark, decision, error,
    evidence_scope: "synthetic Showdown-only experiments; no real Android/Windows E2E",
  };
  const out = process.argv.indexOf("--out");
  if (out !== -1) {
    if (!process.argv[out + 1]) throw new Error("--out requires a path");
    fs.writeFileSync(resolve(process.argv[out + 1]), JSON.stringify(result, null, 2) + "\n", {flag: "w"});
  }
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  if (error || !decision.performance_100_valid) process.exitCode = 2;
}
if (require.main === module) run().catch(e => {process.stderr.write(e.message + "\n");process.exitCode=2;});
module.exports = {connectWorker};
