"""Find the pinned toolkit, and refuse to run without it.

Every board tool that needs `pcbqa` imports this first. The toolkit is consumed
only from the submodule at the path `board/toolchain.json` names - never from a
sibling checkout, never from an absolute path baked into a script, and never
from whatever happens to be on PYTHONPATH. A fresh recursive clone therefore
works, and a checkout without submodules fails immediately with a message that
says what to run rather than an ImportError three frames deep.

`PCB_TOOLKIT_PATH` overrides the location. That exists so the board can be
tested against a local toolkit checkout before the submodule is committed; it
is a development affordance, not a fallback, and nothing in the repository
depends on it being set.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLCHAIN = os.path.join(HERE, "board", "toolchain.json")


def _declared_path():
    with open(TOOLCHAIN, encoding="utf-8") as fh:
        return json.load(fh)["toolkit"]["path"]


def toolkit_root():
    """Absolute path to the toolkit, or raise saying how to get it."""
    override = os.environ.get("PCB_TOOLKIT_PATH")
    if override:
        root = os.path.abspath(override)
        if not os.path.isdir(os.path.join(root, "pcbqa")):
            raise SystemExit(
                "PCB_TOOLKIT_PATH is set to {!r}, which contains no pcbqa "
                "package".format(override))
        return root

    root = os.path.join(HERE, _declared_path().replace("/", os.sep))
    if not os.path.isdir(os.path.join(root, "pcbqa")):
        raise SystemExit(
            "the toolkit submodule is not checked out at {}.\n"
            "Run:  git submodule update --init --recursive".format(
                _declared_path()))
    return root


def install():
    """Put the toolkit on sys.path. Idempotent."""
    root = toolkit_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def toolchain():
    with open(TOOLCHAIN, encoding="utf-8") as fh:
        return json.load(fh)


install()
