"""Tiny shared helpers with no dependency on the rest of the package."""

from __future__ import annotations


def log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(msg)
