"""Settings needed to import workflow_agent in tests, without a real .env.

Set before any test module imports workflow_agent.config. Values already in
the environment win, as with .env.
"""

import os

os.environ.setdefault("WORKFLOW_BASE_URL", "http://workflow.test")
os.environ.setdefault("WORKFLOW_TOKEN", "test-token")
os.environ.setdefault("LLM_MODEL", "test")
os.environ.setdefault("STORE_PATH", ":memory:")
