"""Regression tests; preserve flat application imports without moving user state."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
