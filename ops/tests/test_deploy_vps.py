"""VPS 배포 백엔드(deploy_vps_sync) — 타겟 분기·정적 배포·실 서버 E2E.

Render 종속 제거(2026-07-08 사용자 방향)의 브레인 면. 실제 node 프로세스를
detached로 띄워 헬스·바이트 대조까지 관통 검증한다(스킵 없는 실물 E2E).
"""
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from system import deploy as D


def _env(**kv):
    return mock.patch.dict(os.environ, kv)


class TargetDispatchTest(unittest.TestCase):
    def test_default_is_vps(self):
        with _env(ORGANT_DEPLOY_TARGET=""), \
             mock.patch.object(D, "deploy_vps_sync", return_value="VPS!") as m:
            out = D.deploy_sync("/ws", "n", "g", "u", "rk", "ow")
        self.assertEqual(out, "VPS!")
        m.assert_called_once_with("/ws", "n", "g", "u")

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
