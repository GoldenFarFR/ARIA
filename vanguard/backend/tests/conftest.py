import sys
from pathlib import Path

# backend/ on PYTHONPATH for pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.aria_host import register_aria_host_integrations

register_aria_host_integrations()