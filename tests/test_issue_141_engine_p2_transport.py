"""Synthetic IPC edge tests; real pinned Showdown execution is not asserted."""
from __future__ import annotations

from dataclasses import replace
import time
import unittest

from champions_assist.local_strategy.simulator_adapter import WorkerUnavailable
from tests.test_issue_140_foundation import (
    WorkerTests, VERIFIED_ASSETS, frozen,
)


# A synthetic Python stand-in for Node is ONLY a wire/nonce validator.
REPLY = (
    "import json,hashlib\n"
    "for line in sys.stdin:\n"
    " r=json.loads(line)\n"
    " if r['op']=='HELLO':\n"
    "  r.update(capability_status='PARTIAL',ok=True,payload={})\n"
    " else:\n"
    "  p=r['payload']\n"
    "  h=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,"
    "separators=(',',':'),ensure_ascii=False).encode()).hexdigest()\n"
    "  body={'status':'ENGINE_CHECKED_SIMPLE_CHOICES',"
    "'reason':'ENGINE_REQUEST_AND_EACH_CHOICE_CHECKED',"
    "'commands':['move 1','switch 2'],"
    "'battle_sha256':h(p['battle']),"
    "'request_sha256':'f'*64,"
    "'scope':'PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY',"
    "'exhaustive_legal_choices_verified':False,"
    "'source':'FULL_REPLAY_SYNTHETIC',"
    "'hypothesis_id':p['hypothesis_id'],"
    "'branch_input_sha256':p['branch_input_sha256']}\n"
    "  r.update(capability_status='PARTIAL',ok=True,payload=body,"
    "branch_request_id=r['branch_request_id'],"
    "hypothesis_id=p['hypothesis_id'],"
    "branch_input_sha256=p['branch_input_sha256'])\n"
    " print(json.dumps(r,ensure_ascii=False),flush=True)\n"
)


class EngineP2WorkerTransportTests(unittest.TestCase):
    def test_valid_synthetic_echo_is_partial_and_nonexhaustive(self):
        tmp, worker=WorkerTests().make_fake_worker(REPLY)
        try:
            worker.start()
            s=frozen()
            s=replace(s,window=replace(s.window,
                deadline_monotonic=time.monotonic()+3))
            result=worker.enumerate_p2_from_replay(
                s, {"fixture":"complete-synthetic","rng":[1,2,3,4]},
                assets=VERIFIED_ASSETS)
            self.assertEqual(result.status, "ENGINE_CHECKED_SIMPLE_CHOICES")
            self.assertEqual(result.commands, ("move 1","switch 2"))
            self.assertEqual(result.request_sha256, "f"*64)
            self.assertFalse(result.exhaustive_legal_choices_verified)
        finally:
            worker.close()
            tmp.cleanup()

    def test_wrong_nonce_and_response_hash_never_adopted(self):
        tampered=REPLY.replace(
            "branch_request_id=r['branch_request_id']",
            "branch_request_id='0'*32")
        tmp, worker=WorkerTests().make_fake_worker(tampered)
        try:
            worker.start()
            s=frozen()
            s=replace(s,window=replace(s.window,
                deadline_monotonic=time.monotonic()+0.25))
            with self.assertRaises(WorkerUnavailable):
                worker.enumerate_p2_from_replay(
                    s, {"fixture":"complete-synthetic"},
                    assets=VERIFIED_ASSETS)
        finally:
            worker.close()
            tmp.cleanup()

    def test_unverified_command_and_false_exhaustiveness_both_block(self):
        for replacement in (
            REPLY.replace("'move 1','switch 2'",
                          "'move 1 mega','switch 2'"),
            REPLY.replace(
                "'exhaustive_legal_choices_verified':False",
                "'exhaustive_legal_choices_verified':True"),
            REPLY.replace("'battle_sha256':h(p['battle'])",
                          "'battle_sha256':'0'*64"),
        ):
            tmp, worker=WorkerTests().make_fake_worker(replacement)
            try:
                worker.start()
                s=frozen()
                s=replace(s,window=replace(s.window,
                    deadline_monotonic=time.monotonic()+3))
                with self.assertRaises(WorkerUnavailable):
                    worker.enumerate_p2_from_replay(
                        s, {"fixture":"complete-synthetic"},
                        assets=VERIFIED_ASSETS)
            finally:
                worker.close()
                tmp.cleanup()


if __name__=="__main__":
    unittest.main()
