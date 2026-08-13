from __future__ import annotations

from xinglan.bootstrap import configure_dependencies


def main() -> None:
    configure_dependencies()
    from xinglan.ime_worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
