from __future__ import annotations

from pathlib import Path


# Allow running commands from inside the submitted folder itself:
#   python -m academic_compliance_agent.main
#   python -m unittest academic_compliance_agent.tests.test_workflow
#
# In that situation Python resolves this inner compatibility package first.
# Extending __path__ lets submodules such as app/, tests/, and main.py resolve
# from the outer submitted folder without duplicating source files.
_OUTER_PACKAGE_DIR = Path(__file__).resolve().parents[1]
if str(_OUTER_PACKAGE_DIR) not in __path__:
    __path__.append(str(_OUTER_PACKAGE_DIR))

