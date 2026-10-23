#!/usr/bin/env python3
"""Открывает графический установщик ClamNTK для РЕД ОС."""

from __future__ import annotations

import os
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "cameav-setup"
os.execv(sys.executable, [sys.executable, str(TARGET), *sys.argv[1:]])
