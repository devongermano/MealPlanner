import sys
from pathlib import Path

# Make the tools/fdc scripts importable as plain modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
