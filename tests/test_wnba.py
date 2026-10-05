"""
wnba/index.html -- the WNBA tracker (HIDDEN) -- and WNBA legs on the All
Sports page. The WNBA runs on the same engine, grades the same markets and
earns the same tiles as the NBA, so it is held to exactly the same checks:
this runs tests/test_basketball.py with HOOPS_LEAGUE=wnba.

    python tests/test_wnba.py
"""
import os
import runpy
from pathlib import Path

os.environ["HOOPS_LEAGUE"] = "wnba"
runpy.run_path(str(Path(__file__).resolve().parent / "test_basketball.py"), run_name="__main__")
