"""배포 provider 레지스트리 + VPS 백엔드 — 특정 플랫폼 종속 제거(2026-07-08 사용자 방향).

"하나에 종속되지 않게, 봇이 맘대로 — AWS면 AWS, GCP면 GCP." 레지스트리 라운드트립·
커스텀 provider 등록·script provider 실행(실 명령 E2E)·vps 실 node 기동까지 관통 검증.
"""
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from system import deploy as D


class ProviderRegistryTest(unittest.TestCase):
    def test_builtins_registered(self):
        self.assertEqual(set(D.deploy_targets()) >= {"vps", "render", "script"}, True)

    def test_unknown_target_lists_available(self):
        out = D.deploy_sync("/ws", "n", "g", "u", "rk", "ow", target="frobnicate")
        self.assertIn("모르는 배포 타겟", out)
        self.assertIn("script", out)     # 안내에 사용 가능 목록·script 탈출구가 있어야

    def test_custom_provider_pluggable(self):
        """새 플랫폼은 provider 하나 등록으로 붙는다 — AWS/GCP도 같은 방식(코드 확장점)."""
        class _FakeAws(D.DeployProvider):
            name = "fake-aws"
            def deploy(self, workspace, name, *, creds, config):
                return f"aws-deployed:{name}:{creds.get('extra', {}).get('AWS_REGION')}"
        D.register_provider(_FakeAws())
        try:
            out = D.deploy_sync("/ws", "site", "g", "u", "rk", "ow",
                                target="fake-aws", config={"env": {"AWS_REGION": "ap-northeast-2"}})
            self.assertEqual(out, "aws-deployed:site:ap-northeast-2")
        finally:
            D._PROVIDERS.pop("fake-aws", None)


class ScriptProviderTest(unittest.TestCase):
    def setUp(self):
        self.ws = TemporaryDirectory()
        self.addCleanup(self.ws.cleanup)

    def test_missing_command_rejected(self):
        out = D.deploy_sync(self.ws.name, "n", None, None, None, None, target="script", config={})
        self.assertIn("command가 필요", out)

    def test_script_runs_with_vault_env_and_verifies_url(self):
        """봇이 지정한 배포 명령을 실제 실행 + 금고 env 주입 + URL 검증까지(스킵 없는 E2E).
        AWS CLI 대신 로컬 무해 명령으로 '임의 provider' 경로를 관통한다."""
        ws = Path(self.ws.name)
        (ws / "out.txt").write_text("payload")
        marker = ws / "deployed_marker"
        # 배포 명령: 금고 주입 env(DEPLOY_TOKEN)와 앱 이름을 파일로 떨군다(실 배포의 스탠드인)
        cmd = f'echo "$DEPLOY_TOKEN:$DEPLOY_NAME" > "{marker}"'
        with mock.patch.object(D, "_check_live", return_value=200), \
             mock.patch.object(D, "_url_safe", return_value=True):
            out = D.deploy_sync(str(ws), "myapp", None, None, None, None, target="script",
                                config={"command": cmd, "url": "https://x.example.com",
                                        "env": {"DEPLOY_TOKEN": "sekret"}})
        self.assertIn("배포 성공", out)
        self.assertEqual(marker.read_text().strip(), "sekret:myapp")   # env·이름이 명령에 닿음
        self.assertNotIn("sekret", out)                                 # 로그에서 자격증명 마스킹

    def test_script_failure_surfaces_exit_code(self):
        out = D.deploy_sync(self.ws.name, "n", None, None, None, None, target="script",
                            config={"command": "exit 7"})
        self.assertIn("exit 7", out)


def _env(**kv):
    return mock.patch.dict(os.environ, kv)


class TargetDispatchTest(unittest.TestCase):
    def test_default_is_vps(self):
        with _env(ORGANT_DEPLOY_TARGET=""), \
             mock.patch.object(D, "deploy_vps_sync", return_value="VPS!") as m:
            out = D.deploy_sync("/ws", "n", "g", "u", "rk", "ow")
        self.assertEqual(out, "VPS!")
        m.assert_called_once_with("/ws", "n", "g", "u")

    def test_explicit_target_arg_beats_env(self):
        """호출별 명시가 전역 env보다 우선 — 같은 조직에서 두 타겟을 섞어 쓸 수 있다."""
        with _env(ORGANT_DEPLOY_TARGET="vps"), \
             mock.patch.object(D, "_deploy_render_sync", return_value="R!") as m:
            out = D.deploy_sync("/ws", "n", "g", "u", "rk", "ow", target="render")
        self.assertEqual(out, "R!")
        with _env(ORGANT_DEPLOY_TARGET="render"), \
             mock.patch.object(D, "deploy_vps_sync", return_value="V!"):
            self.assertEqual(D.deploy_sync("/ws", "n", "g", "u", "rk", "ow", target="vps"), "V!")

    def test_render_optin(self):
        with _env(ORGANT_DEPLOY_TARGET="render"), \
             mock.patch.object(D, "_deploy_render_sync", return_value="R!") as m:
            out = D.deploy_sync("/ws", "n", "g", "u", "rk", "ow")
        self.assertEqual(out, "R!")
        m.assert_called_once()


class VpsDeployTest(unittest.TestCase):
    def setUp(self):
        self.apps = TemporaryDirectory()
        self.ws = TemporaryDirectory()
        self.addCleanup(self.apps.cleanup)
        self.addCleanup(self.ws.cleanup)
        self._envp = _env(ORGANT_APPS_DIR=self.apps.name,
                          ORGANT_APPS_BASE_URL="http://127.0.0.1:1/apps")  # 공개 URL은 즉시 거부(비검증 분기)
        self._envp.start()
        self.addCleanup(self._envp.stop)
        # 공개 URL 확인은 즉시 실패(로컬 검증 완료 분기), 사용성 측정은 생략
        self._p1 = mock.patch.object(D, "_check_live", return_value=None)
        self._p2 = mock.patch.object(D, "_measure_usability", return_value="")
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)

    def _registry(self):
        return json.loads((Path(self.apps.name) / "registry.json").read_text())

    def test_static_deploy_no_process(self):
        Path(self.ws.name, "public").mkdir()
        Path(self.ws.name, "public", "index.html").write_text("<h1>hi</h1>")
        out = D.deploy_vps_sync(self.ws.name, "static-demo")
        self.assertIn("배포 성공", out)
        e = self._registry()["static-demo"]
        self.assertTrue(e["static"])
        self.assertIsNone(e["pid"])
        self.assertTrue((Path(e["dir"]) / "public" / "index.html").exists())

    def test_empty_workspace_rejected(self):
        self.assertIn("작업공간이 비어", D.deploy_vps_sync(self.ws.name, "x"))

    def test_server_deploy_end_to_end_and_redeploy(self):
        """실 node 기동 → 헬스 → 로컬 바이트 대조 → 재배포(파일 갱신 반영·포트 유지) → 정리."""
        ws = Path(self.ws.name)
        (ws / "public").mkdir()
        (ws / "public" / "index.html").write_text("v1")
        (ws / "server.js").write_text(
            "const http=require('http'),fs=require('fs'),p=process.env.PORT;\n"
            "http.createServer((q,r)=>{const f='public'+(q.url==='/'?'/index.html':q.url);\n"
            "  try{r.end(fs.readFileSync(f))}catch(e){r.statusCode=404;r.end('no')}\n"
            "}).listen(p);\n")
        out = D.deploy_vps_sync(str(ws), "e2e-demo")
        try:
            self.assertIn("배포 성공", out)
            e = self._registry()["e2e-demo"]
            self.assertTrue(D._pid_alive(e["pid"]))
            # 재배포: 산출물 갱신이 반영되고 포트는 유지된다
            (ws / "public" / "index.html").write_text("v2-updated")
            out2 = D.deploy_vps_sync(str(ws), "e2e-demo")
            self.assertIn("배포 성공", out2)
            e2 = self._registry()["e2e-demo"]
            self.assertEqual(e["port"], e2["port"])
            body = D._local_fetch(e2["port"])(f"x://x/{'index.html'}")
            self.assertEqual(body, b"v2-updated")
        finally:
            D._stop_app(self._registry().get("e2e-demo", {}))

    def test_port_reuse_and_alloc(self):
        reg = {"a": {"port": 4100}, "b": {"port": 4101}}
        self.assertEqual(D._alloc_port(reg, "a"), 4100)   # 기존 앱은 자기 포트 유지
        self.assertEqual(D._alloc_port(reg, "new"), 4102)


if __name__ == "__main__":
    unittest.main()
