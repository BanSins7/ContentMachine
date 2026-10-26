"""Standalone operator entrypoint: python -m intelligence.collector doctor."""
from .cli import main

if __name__=="__main__":
    raise SystemExit(main())
