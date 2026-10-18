import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only")
os.environ["PROVIDERS"] = "fake"

from .settings import *  # noqa: E402,F403
from .settings import REST_FRAMEWORK  # noqa: E402

REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"plan": "1000/min"}}
