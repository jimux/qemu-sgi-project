"""Entry point for running analysis_tools as a module.

Usage:
    python -m analysis_tools <command> [args...]
"""

import sys
from .cli import main

if __name__ == '__main__':
    sys.exit(main())
