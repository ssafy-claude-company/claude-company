"""안전 읽기 도구가 실제로 가리는가 (2026-08-06, 현준-4).

이 도구는 내가 저지른 실수에서 나왔다. 설정을 읽으려고 "키 이름에 secret/key가 들어간 줄을
가린다"는 정규식을 썼는데, LiveKit 설정의 열쇠는 `<이름>: <값>` 꼴이라 이름 쪽에 그 낱말이
없었다 — 그대로 지나쳐 API 시크릿이 대화 기록에 찍혔다.

그러니 이 시험이 재야 하는 것은 하나다: **이름이 무엇이든 값의 모양이면 가려지는가.**
아래 표본은 전부 실제로 만난 꼴이다(env 대입, yaml 이름:값, PEM, URL 속 비밀번호).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safeshow import scrub  # noqa: E402

MASK = "••••[가려진 값]"


class SafeShowTest(unittest.TestCase):
    def _hidden(self, text, secret):
        out = scrub(text)
        self.assertNotIn(secret, out, f"가려지지 않았다: {text!r} → {out!r}")
        return out

    def test_이름에_힌트가_없어도_가린다(self):
        """이번 사고의 꼴 그대로 — 이름이 murmur_xxxx 이고 값이 생 16진수."""
        self._hidden("  murmur_8f1c8f17f2ce: ca6527967bdf93f030eaf1a90f9c7ea1b2e69a4a98fc0618",
                     "ca6527967bdf93f030eaf1a90f9c7ea1b2e69a4a98fc0618")

    def test_env_대입도_가린다(self):
        out = self._hidden("DJANGO_SECRET_KEY=x9Kd2mQpZr7Lf4Nb8Tv1Ws6Ye3Uh5Gj0", "x9Kd2mQpZr7Lf4Nb8Tv1Ws6Ye3Uh5Gj0")
        self.assertIn("DJANGO_SECRET_KEY=", out, "이름까지 가리면 무엇이 설정됐는지 못 본다")

    def test_주소_속_비밀번호도_가린다(self):
        self._hidden("DATABASE_URL=postgresql://murmur:Zx8Kp2Lm9Qr4Tv6Wn@127.0.0.1:5432/murmur",
                     "Zx8Kp2Lm9Qr4Tv6Wn")

    def test_개인키_덩어리는_통째로_가린다(self):
        pem = ("-----BEGIN PRIVATE KEY-----\n"
               "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
               "-----END PRIVATE KEY-----")
        self._hidden(pem, "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ")

    def test_순수_16진수_열쇠도_가린다(self):
        self._hidden("token 0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef")

    def test_사람이_읽는_것은_남긴다(self):
        keep = ("port: 7880\n"
                "LIVEKIT_WS_URL=wss://murmur.dojin-mini.shop/livekit\n"
                "path: /root/ClaudeCompany/ops/systemd\n"
                "use_external_ip: true\n"
                "# 한글 주석은 그대로 남아야 읽을 수 있다\n")
        out = scrub(keep)
        for line in keep.splitlines():
            self.assertIn(line, out, f"읽어야 할 줄이 가려졌다: {line!r}")

    def test_짧은_값은_건드리지_않는다(self):
        self.assertEqual(scrub("level: info\nempty_timeout: 300"),
                         "level: info\nempty_timeout: 300")


if __name__ == "__main__":
    unittest.main()
