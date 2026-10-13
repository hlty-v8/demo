import sys
from pathlib import Path

# The solution lives one level up; tests import it as a plain module so the
# program stays a single self-contained file with no packaging ceremony.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
