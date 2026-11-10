"""AGY CLI 报错信息的可读性守护。

AGY 的 stderr 里混着大量与本次失败无关的噪音（git watcher、遥测、缓存刷新）。
以前 `_read_stderr` 只保留尾部 4096 字节，真正的错误行会被挤出去，
用户看到的报错只剩 ``Get "": EOF``——URL 是空的，完全看不出原因。

现在改成从 stderr 里挑出真正说明问题的那一行，并与 CLI 自报的 error 合并。
"""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from antigravity_cli import _merge_cli_error, extract_error_line


NOISY_STDERR = """E1110 16:44:11.608065       1 errorreport.go:273] Unable to start git event watcher for workspace file:///D:/AionsHome/aion-chat/data/antigravity-login: failed to create git event watcher for D:/AionsHome: core.repositoryformatversion does not support extension: worktreeconfig
W1110 16:44:16.649273     510 cache.go:157] Cache(peopleInfo): Singleflight refresh failed: Get "https://www.googleapis.com/oauth2/v2/userinfo": EOF
E1110 16:44:16.649273     510 errorreport.go:273] Get "https://www.googleapis.com/oauth2/v2/userinfo": EOF
E1110 16:44:16.654067     548 error_parsing.go:87] Error: Get "https://www.googleapis.com/oauth2/v2/userinfo": EOF
E1110 16:44:16.654067     548 server_oauth.go:112] Validation failed: Get "https://www.googleapis.com/oauth2/v2/userinfo": EOF
E1110 16:44:16.687869       1 printmode.go:297] Print mode: eligibility check failed: Eligibility check failed: Get "https://www.googleapis.com/oauth2/v2/userinfo": EOF
"""


class ExtractErrorLineTests(unittest.TestCase):
    def test_picks_the_line_that_explains_the_failure(self):
        line = extract_error_line(NOISY_STDERR)
        self.assertIn("eligibility check failed", line.lower())
        # 关键：URL 必须完整，不能只剩半截
        self.assertIn("https://www.googleapis.com/oauth2/v2/userinfo", line)

    def test_strips_the_log_timestamp_prefix(self):
        line = extract_error_line(NOISY_STDERR)
        self.assertFalse(line.startswith("E1110"))
        self.assertNotIn("printmode.go:297]", line)

    def test_detects_the_not_logged_in_case(self):
        stderr = (
            "E1110 17:57:16.384986     108 errorreport.go:273] Failed to poll "
            "ListExperiments: error getting token source: You are not logged into Antigravity.\n"
        )
        line = extract_error_line(stderr)
        self.assertIn("not logged into Antigravity", line)

    def test_empty_stderr_yields_empty_string(self):
        self.assertEqual("", extract_error_line(""))


class MergeCliErrorTests(unittest.TestCase):
    def test_truncated_cli_error_gets_the_full_stderr_detail(self):
        merged = _merge_cli_error(
            'Eligibility check failed: Get "": EOF', [NOISY_STDERR]
        )
        self.assertIn("https://www.googleapis.com/oauth2/v2/userinfo", merged)

    def test_falls_back_to_cli_error_without_stderr(self):
        self.assertEqual(
            "AGY 调用结束：UNKNOWN",
            _merge_cli_error("AGY 调用结束：UNKNOWN", []),
        )

    def test_uses_stderr_detail_when_cli_error_is_vague(self):
        stderr = (
            "E1110 17:57:16.384986     108 errorreport.go:273] Failed to poll "
            "ListExperiments: error getting token source: You are not logged into Antigravity.\n"
        )
        merged = _merge_cli_error("error: token source", [stderr])
        self.assertIn("not logged into Antigravity", merged)


if __name__ == "__main__":
    unittest.main()
