#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""仅导出 Manna 包内 bootstrap → frontend/mobile-manna/static/bootstrap。

勿用于 Selah。Selah 请用 export_selah_bootstrap.py。
"""
from __future__ import annotations

import sys

from export_bootstrap_snapshot import PRESETS, main as export_main


def main():
    # 强制 Manna preset，忽略误传的其它 argv 中的 --preset
    argv = [a for a in sys.argv[1:] if not a.startswith('--preset')]
    sys.argv = [sys.argv[0], '--preset', 'manna', *argv]
    print(f'Manna bootstrap target → {PRESETS["manna"]["out"]}')
    export_main()


if __name__ == '__main__':
    main()
