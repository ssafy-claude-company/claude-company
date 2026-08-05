"""봇이 쓴 코드는 root로 돌지 않는다 (2026-08-05 감사, 현준-4 — 사용자 지시 '해결해').

러너 본체는 User=organt로 낮춰 두고도 거기서 띄우는 앱에는 아무 사용자 지정이 없어, 배포된
앱이 전부 UID 0으로 떴다(실측 7개 중 6개). 그 앱은 /etc/murmur-web.env(600 root:root)를 그냥
읽는다 — ORGANT_VAULT_KEY(모든 사용자 금고의 열쇠)·DJANGO_SECRET_KEY(서명 위조)·
ORGANT_GUIDE_TOKEN·결제/DB/음성 열쇠가 다 거기 있다. 봇은 사람이 시키는 대로 코드를 쓰므로
한 줄 요청이 서버 장악으로 이어질 수 있었다.

앱을 띄우는 자리는 둘이다(배포·소생). 한쪽만 고치면 소생될 때 다시 root가 되므로 둘 다 잰다.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPAWNERS = ("system/deploy.py", "ops/organt_apps_revive.py")
NEEDED = ("--uid=organt", "--gid=organt", "NoNewPrivileges=yes", "ProtectSystem=full")


class AppNotRootTest(unittest.TestCase):
    def test_앱을_띄우는_모든_자리가_사용자를_낮춘다(self):
        missing = []
        for rel in SPAWNERS:
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("systemd-run", src, f"{rel}: 앱을 띄우는 자리가 아니다(계약 대상 확인)")
            for token in NEEDED:
                if token not in src:
                    missing.append(f"{rel}: {token} 없음")
        self.assertFalse(missing, "앱이 root로 뜰 수 있다:\n  " + "\n  ".join(missing))

    def test_systemd_run을_쓰는_자리가_더_생기면_알린다(self):
        """새 자리가 생기면 위 목록에 넣고 같은 자를 대라 — 모르는 문으로 다시 root가 되지 않게."""
        found = set()
        for p in ROOT.rglob("*.py"):
            s = str(p)
            if any(x in s for x in (".venv", "node_modules", "/var/", "/tests/", "site-packages")):
                continue
            try:
                if "systemd-run" in p.read_text(encoding="utf-8", errors="replace"):
                    found.add(str(p.relative_to(ROOT)))
            except OSError:
                continue
        self.assertEqual(found, set(SPAWNERS),
                         f"systemd-run을 쓰는 자리가 달라졌다: {sorted(found)}")


if __name__ == "__main__":
    unittest.main()
