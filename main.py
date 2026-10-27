"""兼容入口：``python main.py`` 与 ``python -m pcl`` 等价。"""

from __future__ import annotations

from pcl.app import run

if __name__ == "__main__":
    run()
