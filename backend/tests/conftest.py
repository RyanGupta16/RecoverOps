import sys
from pathlib import Path

# Make `app` importable when pytest is run from backend/ or the repo root.
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
