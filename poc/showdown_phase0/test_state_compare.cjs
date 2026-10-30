"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {stableStringify, fingerprint, classifySerializedDivergence} = require("./state_compare.cjs");

// Artificial persisted-state shape: these are comparator regression tests,
// NOT evidence that Showdown serializes every case or that Champions agrees.
function sample() {
  return {
    turn: 4,
    sides: [{pokemon: [{volatiles: {choicelock: {move: "earthquake", duration: 2}},
      status: "par", statusState: {duration: 3}, item: "choicescarf"}],
      sideConditions: {reflect: {duration: 4, sourceSlot: 1}}}],
    field: {weatherState: {id: "raindance", duration: 3},
      pseudoWeather: {trickroom: {duration: 2}}},
    queue: [{choice: "move", moveid: "earthquake", targetLoc: 1}],
    pendingChoices: [{side: "p1", actions: [{choice: "move", moveid: "earthquake"}]}],
    history: [{turn: 3, action: "switch", target: 1}],
  };
}
test("object key insertion order does not cause false mismatch; array order does", () => {
  assert.equal(fingerprint({a: 1, b: 2}), fingerprint({b: 2, a: 1}));
  assert.notEqual(fingerprint([{order: 1}, {order: 2}]), fingerprint([{order: 2}, {order: 1}]));
});
test("negative control: changing choice-lock target must be detected", () => {
  const baseline = sample(), changed = structuredClone(baseline);
  changed.sides[0].pokemon[0].volatiles.choicelock.move = "dragonclaw";
  assert.notEqual(fingerprint(baseline), fingerprint(changed));
});
test("negative control: changing wall/weather/pseudo-weather turns must be detected", () => {
  for (const change of [
    s => {s.sides[0].sideConditions.reflect.duration = 3;},
    s => {s.field.weatherState.duration = 2;},
    s => {s.field.pseudoWeather.trickroom.duration = 1;},
  ]) {
    const original = sample(), changed = structuredClone(original);
    change(changed);
    assert.notEqual(fingerprint(original), fingerprint(changed));
  }
});
test("negative control: changing pending choice or action queue must be detected", () => {
  for (const change of [
    s => {s.pendingChoices[0].actions[0].moveid = "dragonclaw";},
    s => {s.queue[0].moveid = "dragonclaw";},
    s => {s.queue[0].targetLoc = -1;},
    s => {s.queue.push({choice: "switch", target: 2});},
  ]) {
    const original = sample(), changed = structuredClone(original);
    change(changed);
    assert.notEqual(fingerprint(original), fingerprint(changed));
  }
});
test("negative control: status, item consumption and switch history are not ignored", () => {
  for (const change of [
    s => {s.sides[0].pokemon[0].status = "brn";},
    s => {s.sides[0].pokemon[0].item = "";},
    s => {s.history[0].target = 2;},
  ]) {
    const original = sample(), changed = structuredClone(original);
    change(changed);
    assert.notEqual(stableStringify(original), stableStringify(changed));
  }
});

test("log-only replay variance is labeled LOG_ONLY but never equal", () => {
  const left = {turn: 2, queue: [{moveid: "earthquake"}], log: ["|-damage|p2a|50/100"]};
  const right = structuredClone(left);
  right.log[0] = "|-damage|p2a|49/100";
  const x = classifySerializedDivergence(left, right);
  assert.equal(x.kind, "LOG_ONLY");
  assert.equal(x.full_state_equal, false);
  assert.equal(x.non_log_equal, true);
  assert.equal(x.log_equal, false);
});
test("PRNG, queues, effects or HP divergence is a state/queue failure", () => {
  const base = {rng: [1, 2, 3, 4], queue: [{moveid: "earthquake"}],
    field: {weatherState: {duration: 2}}, hp: 60, log: ["move"]};
  for (const mutate of [
    x => {x.rng[2] = 7;},
    x => {x.queue[0].moveid = "dragonclaw";},
    x => {x.field.weatherState.duration = 1;},
    x => {x.hp = 61;},
  ]) {
    const changed = structuredClone(base);
    mutate(changed);
    assert.equal(classifySerializedDivergence(base, changed).kind, "STATE_OR_QUEUE");
  }
});
