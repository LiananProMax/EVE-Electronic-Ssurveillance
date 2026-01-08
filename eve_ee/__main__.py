from __future__ import annotations

from .bootstrap import bootstrap_runtime


def main() -> None:
    ort = bootstrap_runtime()
    from .app import run

    run(ort=ort)


if __name__ == "__main__":
    main()

