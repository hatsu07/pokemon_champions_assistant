"""Synthetic worker transport only: fake replies are NOT engine attestation.

Exact request/nonce/sha and payload checks are exercised without using
Pokémon Champions capture, a full Showdown Battle, or a live assistant.
"""
from __future__ import annotations

from dataclasses import replace
import json
import time
import unittest

from champions_assist.local_strategy.simulator_adapter import WorkerUnavailable
from tests.test_issue_140_foundation import VERIFIED_ASSETS, WorkerTests, frozen

PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1"

REPLY = (
    "import sys,json,hashlib\n"
    "h=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,"
    "ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()\n"
    "for line in sys.stdin:\n"
    " r=json.loads(line)\n"
    " if r['op']=='HELLO':\n"
    "  r.update(capability_status='PARTIAL',ok=True,payload={})\n"
    " else:\n"
    "  p=r['payload']; source=p['battle']\n"
    "  own=p['expected_p1_roster']\n"
    "  roster=[{'side':'p1','slot':i+1,'species_id':m['species_id'],"
    "'move_ids':m['moves'],'hp':80,'maxhp':100,'fainted':False,"
    "'boosts':[[k,0] for k in ('atk','def','spa','spd','spe','accuracy','evasion')]}"
    " for i,m in enumerate(own)]\n"
    "  roster += [{'side':'p2','slot':i+1,'species_id':name,"
    "'move_ids':['protect'],'hp':80,'maxhp':100,'fainted':False,"
    "'boosts':[[k,0] for k in ('atk','def','spa','spd','spe','accuracy','evasion')]}"
    " for i,name in enumerate(('dragonite','gholdengo','kingambit'))]\n"
    "  readout={'status':'SYNTHETIC_ENGINE_REPLAY_READOUT',"
    "'source':'PINNED_SHOWDOWN_COMPLETE_SYNTHETIC_REPLAY',"
    "'engine_sha':'"+PIN+"','battle_sha256':h(source),"
    "'active':{'p1':1,'p2':1},'members':roster,"
    "'full_state_reconstructed':False,'ready_for_engine':False,"
    "'opponent_slot_mapping_authenticated':False,"
    "'unverified':['REAL_CHAMPIONS_BATTLE_ORIGIN_NOT_ATTESTED']}\n"
    "  body={'source':'FULL_REPLAY_SYNTHETIC','hypothesis_id':p['hypothesis_id'],"
    "'branch_input_sha256':p['branch_input_sha256'],"
    "'transport_scope':'VERIFIED_LOCAL_PROCESS_SYNTHETIC_ONLY',"
    "'readout':readout,'full_state_reconstructed':False,'ready_for_engine':False}\n"
    "  r.update(capability_status='PARTIAL',ok=True,payload=body)\n"
    " print(json.dumps(r,ensure_ascii=False),flush=True)\n"
)


class ReadoutWorkerTransportTests(unittest.TestCase):
    def exchange(self, source=REPLY, *, mutate=None):
        tmp, worker=WorkerTests().make_fake_worker(source)
        try:
            worker.start()
            snapshot=frozen()
            snapshot=replace(snapshot,window=replace(snapshot.window,
                deadline_monotonic=time.monotonic()+0.5))
            if mutate:
                snapshot=mutate(snapshot)
            return worker.readout_synthetic_replay(
                snapshot,{"synthetic_complete_battle":True,
                          "rng":[1,2,3,4]},assets=VERIFIED_ASSETS)
        finally:
            worker.close()
            tmp.cleanup()

    def test_readout_bound_to_submitted_request_and_full_synthetic_battle(self):
        receipt=self.exchange()
        submitted=json.loads(receipt.submitted_request_json)
        readout=json.loads(receipt.readout_json)
        self.assertEqual(receipt.match_id,"match-1")
        self.assertEqual(receipt.decision_window_id,"window-1")
        self.assertEqual(receipt.snapshot_sha256,submitted["snapshot_sha256"])
        self.assertEqual(receipt.branch_request_id,submitted["branch_request_id"])
        self.assertEqual(receipt.branch_input_sha256,
                         submitted["payload"]["branch_input_sha256"])
        self.assertEqual(readout["battle_sha256"],receipt.battle_sha256)
        self.assertEqual(submitted["op"],"READOUT_REPLAY")
        self.assertEqual(len(readout["members"]),6)
        self.assertFalse(receipt.engine_battle_authenticated)
        self.assertFalse(receipt.full_state_reconstructed)
        self.assertFalse(receipt.opponent_slot_mapping_authenticated)
        self.assertFalse(receipt.ready_for_engine)

    def test_reject_forged_readout_hash_or_runnable_flags(self):
        for old,new in (
            ("'battle_sha256':h(source)","'battle_sha256':'0'*64"),
            ("'ready_for_engine':False","'ready_for_engine':True"),
            ("'full_state_reconstructed':False","'full_state_reconstructed':True"),
            ("'transport_scope':'VERIFIED_LOCAL_PROCESS_SYNTHETIC_ONLY'",
             "'transport_scope':'REAL_CHAMPIONS_ATTESTED'"),
            ("'source':'PINNED_SHOWDOWN_COMPLETE_SYNTHETIC_REPLAY'",
             "'source':'REAL_CHAMPIONS_OBSERVATION'"),
        ):
            with self.subTest(replacement=old):
                with self.assertRaises(WorkerUnavailable):
                    self.exchange(REPLY.replace(old,new))

    def test_reject_wrong_echo_and_expired_or_unknown_hypothesis(self):
        forged=REPLY.replace(
            "r.update(capability_status='PARTIAL',ok=True,payload=body)",
            "r.update(capability_status='PARTIAL',ok=True,payload=body,"
            "branch_request_id='0'*32)")
        with self.assertRaises(WorkerUnavailable):
            self.exchange(forged)
        with self.assertRaises(WorkerUnavailable):
            self.exchange(mutate=lambda s:replace(
                s,window=replace(s.window,simulator_sha="0"*40)))
        # A hypothesis not present in the frozen source cannot be introduced
        # merely by providing a string to the transport.
        tmp, worker=WorkerTests().make_fake_worker(REPLY)
        try:
            worker.start()
            with self.assertRaises(WorkerUnavailable):
                worker.readout_synthetic_replay(
                    frozen(),{"full_synthetic":True},assets=VERIFIED_ASSETS,
                    hypothesis_id="invented")
        finally:
            worker.close();tmp.cleanup()


if __name__=="__main__":
    unittest.main()
