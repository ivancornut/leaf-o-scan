"""Start LeafScan.  Open this file in any Python editor and press Run, or:

    python run_leafscan.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leafscan.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
