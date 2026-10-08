"""Cylinder Resample, a Maya 2022-2027 modeling tool (Python 3)."""

__version__ = "0.4.1"


def show():
    from .ui import show as show_window
    return show_window()


def self_test():
    from .self_test import run
    return run()
