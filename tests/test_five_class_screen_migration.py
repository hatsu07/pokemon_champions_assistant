"""Non-destructive five-class dataset migration and schema regression tests."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from scripts.prepare_five_class_screen_dataset import build_plan, main, NEW_LABELS as FIVE_LABELS
from scripts.train_screen_classifier import inspect_dataset
from champions_assist.screen_classifier import LABELS


class FiveClassDatasetMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/"seven"
        self.target=Path(self.tmp.name)/"five"
        self.source.mkdir()

    def write(self,label,name="match20261024T120000_12345678__frame.png"):
        directory=self.source/label
        directory.mkdir(parents=True,exist_ok=True)
        file=directory/name
        Image.new("RGB",(16,16),color=(100,20,30)).save(file)
        return file

    def test_exact_five_labels_and_no_unknown_training_class(self):
        self.assertEqual(FIVE_LABELS,("TEAM_SELECTION","TURN_ACTION",
                                 "SWITCH_SELECTION","BATTLE_EVENT","OTHER"))
        self.assertNotIn("UNKNOWN",LABELS)

    def test_dry_run_never_writes_images_or_new_directory(self):
        self.write("MATCHMAKING")
        self.write("TEAM_SELECTION")
        self.write("BATTLE_EVENT")
        self.write("UNKNOWN")
        self.assertEqual(main(["--source",str(self.source),
                               "--destination",str(self.target)]),0)
        self.assertFalse(self.target.exists())
        plan,manual=build_plan(self.source,self.target)
        self.assertEqual(len(plan),2)
        self.assertEqual(manual["BATTLE_EVENT"],1)
        self.assertEqual(manual["UNKNOWN"],1)

    def test_apply_only_copies_unambiguous_preserving_originals(self):
        samples=[
            self.write("MATCHMAKING","match20261024T120000_12345678__queue.png"),
            self.write("TEAM_SELECTION","match20261024T120000_12345678__team.png"),
            self.write("MOVE_MENU","match20261024T120000_12345678__move.png"),
            self.write("PARTY_MENU","match20261024T120000_12345678__switch.png"),
            self.write("RESULT","match20261024T120000_12345678__result.png"),
            self.write("BATTLE_EVENT","match20261024T120000_12345678__event.png"),
            self.write("UNKNOWN","match20261024T120000_12345678__unknown.png"),
        ]
        self.assertEqual(main(["--source",str(self.source),
                               "--destination",str(self.target),"--apply"]),0)
        self.assertEqual({p.name for p in (self.target/"OTHER").glob("*.png")},
                         {samples[0].name,samples[4].name})
        self.assertTrue((self.target/"TEAM_SELECTION"/samples[1].name).exists())
        self.assertTrue((self.target/"TURN_ACTION"/samples[2].name).exists())
        self.assertTrue((self.target/"SWITCH_SELECTION"/samples[3].name).exists())
        self.assertEqual(list((self.target/"BATTLE_EVENT").glob("*.png")),[])
        self.assertTrue(all(p.exists() for p in samples))
        self.assertFalse((self.target/"UNKNOWN").exists())
        self.assertEqual(main(["--source",str(self.source),
                               "--destination",str(self.target),"--apply"]),1)

    def test_collision_before_copy_refuses_and_keeps_source(self):
        a=self.write("MATCHMAKING")
        b=self.write("RESULT",a.name)
        with self.assertRaisesRegex(ValueError,"Collision"):
            build_plan(self.source,self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue(a.exists() and b.exists())

    def test_migrated_trainer_fails_closed_on_remaining_legacy_folder(self):
        self.write("MATCHMAKING")
        self.assertTrue(any("Legacy seven-class images" in issue
                            for issue in inspect_dataset(self.source).report["issues"]))

    def test_migration_refuses_source_equal_to_destination(self):
        self.write("TEAM_SELECTION")
        with self.assertRaisesRegex(ValueError,"separate"):
            build_plan(self.source,self.source)


if __name__=="__main__":
    unittest.main()
