import sys
from pathlib import Path

from toolbloat.config import ServerSpec
from toolbloat.discover import list_tools

TINY = str(Path(__file__).parent / "fixtures" / "tiny_server.py")


def test_lists_tools_without_calling_them():
    [r] = list_tools([ServerSpec("tiny", sys.executable, [TINY])], timeout=60)
    assert r.error is None
    assert sorted(t.name for t in r.tools) == ["add", "shout"]
    assert r.tools[0].input_schema["type"] == "object"
