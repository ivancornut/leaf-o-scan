"""Entry point.

Works both as ``python -m leafscan`` (recommended) and when this file is run
directly, e.g. ``python leafscan/__main__.py`` or the "Run" button of an IDE
(Spyder, VS Code, IDLE).  In the latter case Python does not know that this
file belongs to the ``leafscan`` package, so relative imports fail with
"attempted relative import with no known parent package"; we then add the
project folder to ``sys.path`` and import the package by its absolute name.
"""
if __package__ in (None, ""):
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from leafscan.gui import main
else:
    from .gui import main

if __name__ == "__main__":
    main()
