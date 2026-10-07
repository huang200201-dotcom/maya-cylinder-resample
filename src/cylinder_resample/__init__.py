"""Cylinder Resample, a Maya 2024 modeling tool."""

__version__ = "0.3.0"


def show():
    from .ui import show as show_window
    return show_window()


def self_test():
    from .self_test import run
    return run()
