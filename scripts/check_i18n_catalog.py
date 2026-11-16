"""Fail CI if gettext extraction produces messages absent from the committed POT.

A POT may temporarily retain obsolete entries, but it must include every
message currently extracted from the sources.
"""
import sys
from pathlib import Path

from babel.messages.pofile import read_po


def messages(path):
    with Path(path).open("rb") as stream:
        return {
            entry.id
            for entry in read_po(stream)
            if isinstance(entry.id, str) and entry.id
        }


if __name__ == "__main__":
    extracted, committed = map(messages, sys.argv[1:3])
    missing = sorted(extracted - committed)
    if missing:
        print("Missing gettext messages in translations/messages.pot:")
        for msg in missing:
            print(" -", repr(msg))
        raise SystemExit(1)
    print(f"gettext catalog covers {len(extracted)} extracted messages")
