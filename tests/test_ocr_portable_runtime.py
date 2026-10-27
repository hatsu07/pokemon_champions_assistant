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
        from types import SimpleNamespace
        reader=TesseractJapaneseOcr()
        reader.command="tesseract"
        reader.tessdata_dir="tessdata"
        sample=np.full((32,40,3),120,dtype=np.float32)[:,::2,0:1]
        tsv="level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv+="5\t1\t1\t1\t1\t1\t0\t0\t20\t32\t98\tHP\n"
        with patch("champions_assist.ocr.subprocess.run",
                   return_value=SimpleNamespace(returncode=0,stdout=tsv,stderr="")) as cmd:
            result=reader.read(sample,roi_name="MESSAGE")
        self.assertEqual(result.text,"HP")
        self.assertAlmostEqual(result.confidence,0.98)
        args=cmd.call_args.args[0]
        self.assertEqual(args[0],"tesseract")
        self.assertIn("tsv",args)
        self.assertIn("--tessdata-dir",args)
        self.assertEqual(cmd.call_args.kwargs["env"]["TESSDATA_PREFIX"],"tessdata")

    def test_missing_tessdata_reports_safe_reason(self):
        import numpy as np
        from types import SimpleNamespace
        from champions_assist.ocr import OcrExecutionError
        reader=TesseractJapaneseOcr()
        reader.command="tesseract"
        reader.tessdata_dir="tessdata"
        with patch("champions_assist.ocr.subprocess.run",
                   return_value=SimpleNamespace(returncode=1,stdout="",
                       stderr="Error opening data file C:/private/name/tessdata/jpn.traineddata")):
            with self.assertRaises(OcrExecutionError) as ctx:
                reader.read(np.zeros((32,48),dtype=np.uint8),roi_name="MESSAGE")
        self.assertEqual(ctx.exception.code,"OCR_TESSDATA_LOAD_FAILED")
        self.assertNotIn("C:/private",str(ctx.exception))

    def test_windows_smoke_separates_native_execution_and_glyph_accuracy(self):
        """Synthetic ASCII misreads cannot be confused with Tesseract exit 1."""
        from types import SimpleNamespace
        from scripts.windows_desktop_entry import probe_bundled_ocr_execution
        from champions_assist.ocr import OcrExecutionError
        from unittest.mock import MagicMock
        for value,expected in (("",False),("79%",False),("97%",True)):
            with self.subTest(value=value):
                mock=MagicMock()
                mock.read.return_value=SimpleNamespace(
                    engine="tesseract-jpn+eng",text=value,confidence=None)
                with patch("champions_assist.ocr.TesseractJapaneseOcr",
                           return_value=mock):
                    report=probe_bundled_ocr_execution()
                self.assertEqual(report["bundled_native_ocr"],
                                 "OCR_NATIVE_EXECUTION_OK")
                self.assertIs(report["synthetic_numeric_recognized"],expected)
                mock.read.assert_called_once()
        broken=MagicMock()
        broken.read.side_effect=OcrExecutionError(
            "OCR_TESSDATA_LOAD_FAILED",status=1)
        with patch("champions_assist.ocr.TesseractJapaneseOcr",
                   return_value=broken):
            with self.assertRaises(OcrExecutionError):
                probe_bundled_ocr_execution()

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
