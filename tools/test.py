"""Run all synthetic regressions against this checkout's only runtime."""
from pathlib import Path
import sys, unittest
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'skills/korean-subtitles/scripts'),str(root/'tests')]
result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(root/'tests')))
raise SystemExit(not result.wasSuccessful())
