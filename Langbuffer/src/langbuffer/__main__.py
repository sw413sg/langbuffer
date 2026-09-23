"""Langbuffer entry point."""
import multiprocessing
from .integrated import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
