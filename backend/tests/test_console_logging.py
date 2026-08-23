import io
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.services.console_logging import safe_console_print


class GbkConsole(io.StringIO):
    encoding = "gbk"

    def write(self, text):
        text.encode(self.encoding)
        return super().write(text)


def test_safe_console_print_replaces_unencodable_unicode():
    stream = GbkConsole()

    safe_console_print("[proxy] 🇯🇵 节点切换失败", stream=stream)

    assert "节点切换失败" in stream.getvalue()
    assert "🇯🇵" not in stream.getvalue()
