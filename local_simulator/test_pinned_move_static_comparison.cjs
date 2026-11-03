"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {comparePinnedChampionsStaticMoves} =
  require("./pinned_move_static_comparison.cjs");

function fixture() {
  const slots = [];
  const dexEntries = new Map();
  for (let member = 0; member < 6; member++) {
    for (let slot = 1; slot <= 4; slot++) {
      const move_id = "mv" + member + slot;
      const item = {
        registered_member:"Member" + member, slot, champions_name:"技" + slot,
        move_id, type:"Normal", category:"Physical",
        base_power:70, accuracy:100, pp:20, priority:0,
      };
      slots.push(item);
      dexEntries.set(move_id, {
        id:move_id, exists:true, type:"Normal", category:"Physical",
        basePower:70, accuracy:100, pp:20, priority:0,
      });
    }
  }
  return {
    report:{
      status:"CHAMPIONS_STATIC_24_SOURCE_BOUND",
      team_sha256:"a".repeat(64),
      champions_move_master_sha256:"b".repeat(64),
      slots, source_fields_verified:true,
      champions_engine_parity_verified:false, ranking_status:"NO_RANKING",
    },
    dex:{moves:{get:id=>dexEntries.get(id)}}, dexEntries,
  };
}

test("24 exact static fields, but no gameplay authority", () => {
  const {report,dex} = fixture();
  const out = comparePinnedChampionsStaticMoves(report,dex);
  assert.equal(out.status, "PINNED_STATIC_FIELDS_EQUAL_UNATTESTED");
  assert.equal(out.checked_slots,24);
  assert.equal(out.equal_slots,24);
  assert.equal(out.game_mechanics_verified,false);
  assert.equal(out.ready_for_live,false);
  assert.equal(out.ranking_status,"NO_RANKING");
});

test("mismatched semantics and missing engine entries are explicit", () => {
  const {report,dex,dexEntries} = fixture();
  dexEntries.get("mv01").pp = 15;
  dexEntries.delete("mv12");
  const out = comparePinnedChampionsStaticMoves(report,dex);
  assert.equal(out.status,"PINNED_STATIC_IDS_MISSING");
  assert.equal(out.equal_slots,22);
  assert.deepEqual(out.mismatched,[{slot:"Member0:1",fields:["pp"]}]);
  assert.deepEqual(out.missing,["Member1:2"]);
  assert.equal(out.ready_for_live,false);
});

test("forged verified markers, wrong 24 slots and duplicate slots refused", () => {
  const {report,dex} = fixture();
  for (const bad of [
    {...report,champions_engine_parity_verified:true},
    {...report,source_fields_verified:false},
    {...report,slots:report.slots.slice(1)},
    {...report,slots:[...report.slots.slice(0,23),report.slots[0]]},
    {...report,team_sha256:"not-a-hash"},
    {...report,slots:report.slots.map((x,i)=>i===0?{...x,accuracy:0}:x)},
  ]) {
    const out=comparePinnedChampionsStaticMoves(bad,dex);
    assert.equal(out.status,"PINNED_STATIC_CHECK_REFUSED");
    assert.equal(out.checked_slots,0);
    assert.equal(out.ready_for_live,false);
  }
});
