# Deliberately dirty. The violations below are stable across ruff releases:
# two unused imports (F401) and a print call (T201), all in Odoo's select list.
import os
import sys


def shout(message):
    print(message)
