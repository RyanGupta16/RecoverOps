import os
import sys
from pathlib import Path

import pytest

# Make `app` importable when pytest is run from backend/ or the repo root.
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# The suite must behave identically on a machine with API keys and one without.
# app.runtime loads backend/.env at import time, so the keys are cleared before
# anything imports it; tests that need a live client inject a fake one.
for _var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
    os.environ.pop(_var, None)
os.environ["RECOVEROPS_NO_DOTENV"] = "1"
os.environ["RECOVEROPS_SKIP_FIRST_BATCH"] = "1"


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    """Belt and braces: a test that constructs a layer directly still sees no keys."""
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True, scope="session")
def _isolate_upload_dir(tmp_path_factory):
    """FileSource defaults to backend/data/uploads — a real directory the app
    reads at runtime. Left alone, the suite writes uploaded fixtures into the
    developer's own data (79 stray files before this was noticed) and tests
    read each other's uploads. Point the default at a throwaway directory for
    the whole session."""
    import app.sources as sources

    original = sources.UPLOAD_DIR
    scratch = tmp_path_factory.mktemp("uploads")
    sources.UPLOAD_DIR = scratch
    yield
    sources.UPLOAD_DIR = original
