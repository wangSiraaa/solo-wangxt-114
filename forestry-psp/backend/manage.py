#!/usr/bin/env python3
import os
import sys
from pathlib import Path


def main():
    # 使仓库根目录的 core 计算引擎包可导入
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings_dev"),
    )
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
