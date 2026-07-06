"""브레인 횡단 검증 — 배포 판정(deploy_service_name·_deploy_infeasibility)·표현 유틸
(_speech_clip·_looks_transient)·audit(JSONL·redact)·config(_require·격리).

PJT tests/test_sys.py 순수부 + tests/test_audit.py·tests/test_config.py의 unittest 포팅.

실행:
  cd /root/murmur-stack && PYTHONPATH=/root/murmur-stack \
  /root/murmur-stack/.venv/bin/python -m unittest discover -s system/tests -t /root/murmur-stack -v
"""
import asyncio
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from system.audit import AuditLog, make_post_tool_use_hook, redact_tool_input
from system.guide_tools import (_deploy_infeasibility, _looks_transient,
                                _speech_clip, deploy_service_name)
from system.tests.test_comm import FakeGuide, _flow


class DeployJudgeTest(unittest.TestCase):

    def test_배포명은_프로젝트별_결정적(self):
        """[멀티 프로젝트] 배포 서비스명은 '프로젝트 신원'에서만 결정적으로 유도된다 — 미등록
        흐름은 슬롯이 없다. 과거의 DEPLOY_NAME env·인자·기본 폴백은 폐지됨(공유 슬롯 덮어쓰기 위험)."""
        saved = os.environ.get("DEPLOY_NAME")
        os.environ["DEPLOY_NAME"] = "todo-organt-demo"      # env가 있어도 어디서도 안 읽는다
        try:
            f = _flow(FakeGuide())
            f.project_id, f.project_name = "P-003", "Cell Grow Game"
            assert deploy_service_name(f, "agent-random-name") == "organt-p-003"   # 신원=번호(작명·인자 무시)
            f.project_name = "세포 키우기"                                          # 한글 → 식별번호 폴백
            assert deploy_service_name(f) == "organt-p-003"
            f2 = _flow(FakeGuide())                                                 # 미등록 흐름
            assert deploy_service_name(f2, "x") == ""                  # 슬롯 없음 — env·인자 폴백 폐지
            assert deploy_service_name(f2, "My App!") == ""
        finally:
            if saved is None:
                os.environ.pop("DEPLOY_NAME", None)
            else:
                os.environ["DEPLOY_NAME"] = saved

    def test_배포_타겟_호환_사전검증_런타임Python_차단(self):
        """[P-028] Render Node 런타임엔 Python이 없다 — 서버가 런타임에 Python을 spawn하거나
        start가 Python류면 배포 전 차단. 빌드타임 학습용 Python은 통과."""
        def _ws(files):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
            for name, content in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                    fh.write(content)
            return d
        # ① Node 서버가 런타임에 python spawn → 불가
        d1 = _ws({"package.json": json.dumps({"scripts": {"start": "node server.js"}}),
                  "server.js": "const {spawn}=require('child_process'); const py=spawn('python',['m.py']);"})
        assert "spawn/exec" in _deploy_infeasibility(d1)
        # ② start 커맨드가 gunicorn(Python) → 불가
        d2 = _ws({"package.json": json.dumps({"scripts": {"start": "gunicorn app:app"}})})
        assert "Python류를 실행" in _deploy_infeasibility(d2)
        # ③ 깨끗한 Node 앱(express, node 서빙) → 통과
        d3 = _ws({"package.json": json.dumps({"scripts": {"start": "node server.js"}}),
                  "server.js": "const express=require('express'); express().listen(process.env.PORT);"})
        assert _deploy_infeasibility(d3) == ""
        # ④ 빌드타임 학습용 Python(train.py)만 있고 서빙은 Node → 통과(런타임 의존 아님)
        d4 = _ws({"package.json": json.dumps({"scripts": {"start": "node server.js"}}),
                  "server.js": "const express=require('express'); express().listen(process.env.PORT);",
                  "train.py": "import sklearn  # 빌드타임 오프라인 학습"})
        assert _deploy_infeasibility(d4) == ""


class ExpressionUtilTest(unittest.TestCase):

    def test_발언_안전망은_침묵절단하지_않는다(self):
        """[회의 품질] 발언 클립은 폭주만 막고, 잘리면 '잘렸다'고 표기한다 — 종전 하드컷([:300])의
        침묵 절단(라이브: 전 발언 307~308자 박제) 교정."""
        assert _speech_clip("  짧은 발언  ") == "짧은 발언"            # 무손실 + 트림
        long = "가" * 2000
        out = _speech_clip(long)
        assert out.startswith("가" * 1500) and "2000자" in out and "잘림" in out   # 명시 마커
        assert _speech_clip("나" * 1500) == "나" * 1500               # 경계는 무손실
        assert _speech_clip(None) == ""

    def test_looks_transient_일시적API오류_탐지(self):
        """동료 응답이 일시적 API 오류로 보이면 답으로 취급하지 말고 재시도 — 판정 함수 고정."""
        assert _looks_transient("API Error: overloaded") is True         # 대소문자 무시
        assert _looks_transient("(동료 처리 중 오류: 자동 재시도)") is True
        assert _looks_transient("정상 보고입니다") is False
        assert _looks_transient("") is False and _looks_transient(None) is False


class AuditTest(unittest.TestCase):
    """audit 검증: JSONL 기록 + PostToolUse 훅 (오프라인)."""

    def test_record가_JSONL로_누적(self):
        with tempfile.TemporaryDirectory() as d:
            log = AuditLog(Path(d) / "audit.jsonl")
            log.record("collect", author="사람", content="안녕")
            log.record("route", author="사람")
            lines = (Path(d) / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 2
            e0 = json.loads(lines[0])
            assert e0["event"] == "collect" and e0["author"] == "사람" and "ts" in e0
            assert json.loads(lines[1])["event"] == "route"

    def test_PostToolUse_훅이_툴호출_기록(self):
        with tempfile.TemporaryDirectory() as d:
            log = AuditLog(Path(d) / "a.jsonl")
            hook = make_post_tool_use_hook(log)
            out = asyncio.run(hook(
                {"hook_event_name": "PostToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": "x.txt"}},
                "tu_1", None,
            ))
            assert out == {}
            e = json.loads((Path(d) / "a.jsonl").read_text(encoding="utf-8").strip())
            assert e["event"] == "tool_use" and e["tool"] == "Write"
            assert e["tool_use_id"] == "tu_1"

    def test_PostToolUse_훅이_행위자_기록(self):
        """actor/role를 주면 '누가' 그 툴을 호출했는지 로그에 남는다 — 협업 관찰성."""
        with tempfile.TemporaryDirectory() as d:
            log = AuditLog(Path(d) / "a.jsonl")
            hook = make_post_tool_use_hook(log, actor=12345, role="봇 AI 전문가(먹이탐색)")
            asyncio.run(hook(
                {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                 "tool_input": {"file_path": "server.js"}}, "tu_2", None,
            ))
            e = json.loads((Path(d) / "a.jsonl").read_text(encoding="utf-8").strip())
            assert e["actor"] == 12345 and e["role"] == "봇 AI 전문가(먹이탐색)"
            assert e["tool"] == "Edit"

    def test_redact_tool_input은_파일내용을_길이로_요약한다(self):
        """보안 핫픽스: 감사에 Write/Edit의 파일 내용 전체를 남기지 않고 길이로 요약(경로·도구는 보존)."""
        big = "x" * 500
        out = redact_tool_input({"file_path": "/ws/a.js", "content": big})
        assert out["file_path"] == "/ws/a.js"                      # 경로 보존
        assert "500" in out["content"] and "chars" in out["content"]   # 내용 → 길이요약
        assert "xxxx" not in str(out)                              # 원본 내용 없음
        out2 = redact_tool_input({"old_string": "y" * 200, "new_string": "z" * 200})
        assert "chars" in out2["old_string"] and "chars" in out2["new_string"]
        assert redact_tool_input({"content": "short"})["content"] == "short"   # 짧은 값 보존
        assert redact_tool_input("notdict") == "notdict"          # 비-dict 그대로


class ConfigTest(unittest.TestCase):
    """config 모듈 검증 — _require(필수 env)·작업공간 격리. 실제 repo의 .env로부터 헤르메틱:
    ROOT를 .env 없는 임시 디렉토리로 돌려 운영 시크릿이 '필수 누락' 케이스를 무효화하지 않게 한다."""

    KEYS = ("SYSTEM_BOT", "CHANNEL_ID", "ORGANT_MODEL", "ORGANT_WORKSPACE")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import system.config as config
        importlib.reload(config)     # ROOT 등 모듈 상태 원복(monkeypatch.setattr 자동복원 대응)

    def _load(self, **env):
        """주어진 환경변수만 세팅한 뒤 config 모듈을 새로 로드한다(작업공간·로그 mkdir도 임시 쪽으로)."""
        for key in self.KEYS:
            os.environ.pop(key, None)
        for key, value in env.items():
            os.environ[key] = value
        import system.config as config
        importlib.reload(config)
        tmp = tempfile.mkdtemp(prefix="organt-config-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        config.ROOT = Path(tmp)
        return config

    def test_정상_로딩(self):
        config = self._load(SYSTEM_BOT="sys-token", CHANNEL_ID="123", ORGANT_MODEL="opus")
        cfg = config.load_config()
        assert cfg.system_bot_token == "sys-token"
        assert cfg.channel_id == 123
        assert cfg.model == "opus"

    def test_모델_미설정시_None(self):
        config = self._load(SYSTEM_BOT="s", CHANNEL_ID="1")
        assert config.load_config().model is None

    def test_채널ID_정수변환(self):
        config = self._load(SYSTEM_BOT="s", CHANNEL_ID="987654321")
        cfg = config.load_config()
        assert isinstance(cfg.channel_id, int)
        assert cfg.channel_id == 987654321

    def test_필수_누락시_에러(self):
        config = self._load(CHANNEL_ID="1")  # SYSTEM_BOT 누락 → _require가 RuntimeError
        with self.assertRaises(RuntimeError):
            config.load_config()

    def test_작업공간은_repo_밖_격리(self):
        config = self._load(SYSTEM_BOT="s", CHANNEL_ID="1")
        cfg = config.load_config()
        # repo 루트가 작업공간의 상위 경로에 없어야 한다(= repo 밖).
        assert config.ROOT not in cfg.workspace_dir.parents

    def test_작업공간_env_override(self):
        tmp = Path(tempfile.mkdtemp(prefix="organt-ws-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        config = self._load(SYSTEM_BOT="s", CHANNEL_ID="1",
                            ORGANT_WORKSPACE=str(tmp / "myws"))
        assert config.load_config().workspace_dir == tmp / "myws"


if __name__ == "__main__":
    unittest.main()
