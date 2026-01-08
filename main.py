from __future__ import annotations

from eve_ee.bootstrap import bootstrap_runtime


def main() -> None:
    ort = bootstrap_runtime()

    # 延迟导入：确保 bootstrap 先执行，再加载 Qt / RapidOCR 相关模块
    from eve_ee.app import run

    run(ort=ort)


if __name__ == "__main__":
    main()

