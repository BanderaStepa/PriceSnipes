"""Entry point: python pricecheck.py [--no-email] [--only 2,6c,F3] [--headed] [--test-email]"""
import sys

from pricecheck.runner import main

if __name__ == "__main__":
    sys.exit(main())
