"""Unverified local OCR candidates are species-scoped, never certified facts."""
from __future__ import annotations

import unittest
from champions_assist.battle_state_tracker import LocalBattleStateTracker
from champions_assist.local_event_worker import LocalFieldRead


class LocalOcrSpeciesTests(unittest.TestCase):
    def test_hp_fraction_is_scoped_to_last_verified_active_only_as_candidate(self):
        tracker=LocalBattleStateTracker()
        row=LocalFieldRead(
            field="screen_text.own_hp_fraction",value={"current":0,"maximum":201},
            frame_id="event-frame",timestamp=2.0,confidence=0.8,
            roi="own_hp_fraction",source="ROI_OCR_UNVERIFIED",text="0/201")
        tracker.ingest((row,))
        observation=tracker.snapshot(
            own_active="ガブリアス",own_names=("ガブリアス","アシレーヌ"))
        fact=observation["local_observations"][0]
        self.assertEqual(fact["species_candidate"],"ガブリアス")
        self.assertEqual(fact["candidate_side"],"own")
        self.assertEqual(fact["certainty"],"CANDIDATE")
        self.assertFalse(fact["game_validated"])
        self.assertNotIn("fainted",observation)
        self.assertNotIn("species_candidate",tracker.snapshot()["local_observations"][0])


if __name__=="__main__":
    unittest.main()
