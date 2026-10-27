"""Portable OCR discovery is offline and never runs on the action screen."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from champions_assist.ocr import TesseractJapaneseOcr,discover_japanese_tesseract


class PortableJapaneseOcrTests(TestCase):
    def test_ocr_sanitizes_noncontiguous_and_single_channel_uint8(self):
        import numpy as np
        from unittest.mock import MagicMock
        import pytesseract
        reader=TesseractJapaneseOcr()
        reader.command="tesseract"
        reader.tessdata_dir="tessdata"
        sample=np.full((12,13,3),120,dtype=np.float32)[:,::2,0:1]
        with patch("pytesseract.image_to_data",return_value={"text":["test"],"conf":["98"]}) as read:
            result=reader.read(sample,roi_name="MESSAGE")
        self.assertEqual(result.text,"test")
        passed=read.call_args.args[0]
        self.assertEqual(passed.dtype,np.uint8)
        self.assertTrue(passed.flags["C_CONTIGUOUS"])
        self.assertEqual(passed.ndim,2)

    def test_bundled_runtime_requires_both_language_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/"ocr-runtime"
            models=base/"tessdata"
            models.mkdir(parents=True)
            binary=base/("tesseract.exe" if sys.platform=="win32" else "tesseract")
            binary.write_bytes(b"synthetic binary")
            (models/"eng.traineddata").write_bytes(b"en")
            with patch.object(sys,"_MEIPASS",tmp,create=True),patch(
                    "champions_assist.ocr.shutil.which",return_value=None):
                self.assertIsNone(discover_japanese_tesseract())
                (models/"jpn.traineddata").write_bytes(b"jp")
                discovered=discover_japanese_tesseract()
                self.assertEqual(discovered,(str(binary),str(models)))
                reader=TesseractJapaneseOcr()
                self.assertEqual(reader.command,str(binary))
                self.assertEqual(reader.languages,"jpn+eng")
                self.assertEqual(reader.tessdata_dir,str(models))


if __name__=="__main__":
    import unittest
    unittest.main()
