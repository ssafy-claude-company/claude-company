"""[M9 분할 — task.py에서 추출] complete_task 게이트(_gate_*)·예측함수(_has_*·_wants_*·_synthesizes_data 등)·체크포인트(_ckpt)·역량적립(_ledger_accrue).
파사드 보존: task.py가 이 모듈을 재수출해 기존 import 경로(tests·complete_task) 유지. 세트 밖 task.py 정의 참조 0(순환 없음).
헬퍼(_react·_speech_clip·_jobs_of 등)는 각 함수 내부 지역 import로 함께 이동."""
import os
import re
from typing import List, Optional


_ASSET_EXTS = {
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".mid", ".midi",            # 사운드
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif", ".tga", ".svg",    # 이미지
    ".glb", ".gltf", ".obj", ".fbx", ".dae",                                              # 3D
    ".ttf", ".otf", ".woff", ".woff2",                                                    # 폰트
    ".mp4", ".webm", ".mov", ".ogv", ".m4v",                                              # 영상
    ".aseprite", ".psd", ".xcf",                                                          # 소스 아트
}

def _has_real_asset(workspace) -> bool:
    """작업공간에 코드 아닌 실제 제작 자원 파일(_ASSET_EXTS)이 하나라도 있으면 True — 합성 placeholder가
    아닌 '받아온 실재 자원'의 증거. node_modules·.git은 제외(의존성 번들의 에셋은 우리 제작물 아님)."""
    import os
    if not workspace:
        return False
    try:
        for root, dirs, files in os.walk(str(workspace)):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", ".cache", "dist", "build")]
            for f in files:
                if os.path.splitext(f)[1].lower() in _ASSET_EXTS:
                    return True
    except Exception:
        return False
    return False

def _has_visual_runtime(workspace) -> bool:
    """작업공간이 사용자가 *화면으로 보는* 웹 UI를 렌더하나 — .html 진입점이 있으면 True. 시각 산출물은
    presence·로직 검증만으론 부족하다(실제 렌더가 헤드리스 WebGL/GPU에서 검게/빈 화면으로 나올 수 있음 —
    라이브 P-003). 특정 장르·직군 하드코딩 아님(웹 UI = 사용자가 봄 = 시각 차원)."""
    import os
    if not workspace:
        return False
    try:
        for root, dirs, files in os.walk(str(workspace)):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", ".cache", "dist", "build")]
            if any(f.lower().endswith(".html") for f in files):
                return True
    except Exception:
        return False
    return False

_PERCEPT_AUDIO_HINTS = ("사운드", "음악", "음향", "효과음", "배경음", "오디오",
                        "sound", "audio", "music", "bgm", "sfx")

def _perceptual_essential(labels, texts) -> bool:
    """이 작품에 'LLM이 지각 못 하는 오디오 차원'이 정말 있는가 — 팀이 그 직군을 두었거나(라벨) 합의
    기준·원문이 소리·음악을 명시(텍스트)하면 True. 도메인 중립(팀 자신의 신호로 판정)."""
    hay = " ".join([str(x).lower() for x in (labels or [])] +
                   [str(t).lower() for t in (texts or [])])
    return any(k in hay for k in _PERCEPT_AUDIO_HINTS)

_DATA_FILE_EXTS = {".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".feather", ".arrow", ".jsonl"}

_SYNTH_MARKERS = ("합성 데이터", "합성데이터", "synthetic data", "synthetic_data", "더미 데이터",
                  "dummy data", "가짜 데이터", "fake data", "임의 생성", "랜덤 생성", "데이터 합성",
                  "generate_price", "generate_data", "make_synthetic", "fabricat", "mock data")

def _wants_real_data(text) -> bool:
    """요청/목표가 '실제·공공 데이터로 학습'을 요구하는가 — 데이터 출처 게이트의 발동 조건(도메인 중립)."""
    t = str(text or "").lower()
    real = any(k in t for k in ("공공데이터", "공공 데이터", "공개 데이터", "공개데이터", "오픈 데이터",
                                "오픈데이터", "open data", "실데이터", "실 데이터", "실거래", "real data",
                                "real-world", "공공 api", "data.go.kr", "공공 데이타", "공공데이타"))
    learn = any(k in t for k in ("학습", "train", " ai", "모델", "model", "예측", "predict", "데이터셋", "dataset"))
    return real and learn

def _has_real_dataset(workspace) -> bool:
    """작업공간에 '받아온 실제 데이터 파일'(csv/parquet 등, 빈 스텁 아님)이 있는가 — 합성이 아닌 증거."""
    import os
    if not workspace:
        return False
    try:
        for root, dirs, files in os.walk(str(workspace)):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", ".cache", "dist", "build")]
            for f in files:
                if os.path.splitext(f)[1].lower() in _DATA_FILE_EXTS:
                    try:
                        if os.path.getsize(os.path.join(root, f)) > 2048:   # 빈/스텁 파일은 증거 아님
                            return True
                    except OSError:
                        return True
    except Exception:
        return False
    return False

def _synthesizes_data(workspace):
    """작업공간 코드(.py/.js/.ts)가 학습 데이터셋을 '지어내는'(합성·더미) 흔적 — (파일명, 표식) 또는 None."""
    import os
    if not workspace:
        return None
    try:
        for root, dirs, files in os.walk(str(workspace)):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", ".cache", "dist", "build")]
            for f in files:
                if os.path.splitext(f)[1].lower() not in (".py", ".js", ".ts", ".mjs"):
                    continue
                try:
                    txt = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read().lower()
                except OSError:
                    continue
                for m in _SYNTH_MARKERS:
                    if m in txt:
                        return (f, m)
    except Exception:
        return None
    return None

_VERIFIER_HINTS = ("qa", "검증", "품질", "테스트", "테스터", "quality", "tester", "verif", "정합성")

def _is_verifier(label) -> bool:
    """역할 라벨이 '검증/품질(QA) 기능'인가 — 전체·사용자관점 최종 인수의 자연 담당."""
    t = str(label or "").lower()
    return any(h in t for h in _VERIFIER_HINTS)

def _ckpt(flow):
    """[크래시-세이프 Task 체크포인트] Task 전이(생성·목표확정·owner 확정·마감)마다 미완 Task를
    레지스트리에 영속한다 — 종전엔 흐름 '종료'에만 써서, 동면·강제종료처럼 마감 코드가 못 도는
    죽음이면 진행 중 Task의 정체(블록·스레드·owner·Goal)가 유실돼 복구가 '같은 Task 이어가기'가
    아니라 '새 Task'로 시작했다(라이브 관측 — 사용자 지적). 콜백은 SYS가 주입(미주입이면 무해)."""
    fn = getattr(flow, "checkpoint_task", None)
    if fn:
        try:
            fn()
        except Exception:
            pass

def _ledger_accrue(flow):
    """[B-21 capability ledger — 품질 게이트된 적립(BOT_ARCH_REDESIGN 2026-07-03, 부록 A-5 한정판)]
    마감 확정 Task의 능력 증거(flow.cap_evidence — audit PostToolUse 파생)를 영속 장부로 넘긴다.
    적립 조건: 그 Work를 **owner로 정당 수임해 인도(owner_delivered)** 했고 **교차검증 통과
    (cross_checks>0)** 한 Task에 귀속된 **owner 본인 저작**만. 흡수형(비owner) 저작 증거는 Task
    경계에서 통째로 폐기돼 적립 0 — 흡수 사고 저작이 실적으로 세탁돼 게이트를 뚫는 경로(P-010형)
    차단. 용도는 cover 비편입 4종(peers 강점줄·_free_alternatives 후보 나열·recommend 투영·관측)뿐 —
    _capability_gaps·_offdomain_capability_hit의 cover 판정은 이 장부를 보지 않는다(무변경)."""
    ev = getattr(flow, "cap_evidence", None)
    if not isinstance(ev, dict):
        return
    t = flow.current
    if (t is not None and t.owner and t.owner_delivered and t.cross_checks > 0
            and getattr(flow, "persist_capability", None)):
        own = ev.get(t.owner) or {}
        if own:
            try:
                flow.persist_capability(int(t.owner), dict(own))
            except Exception:
                pass
    ev.clear()   # Task 경계 — 남은(비owner 포함) 증거는 다음 Task로 이월 금지

def _gate_verified(flow):
    """[게이트] run 실행 증거 — 산출물을 run으로 한 번도 실행하지 않았으면 완료 거부(허위 완료 금지).
    에러문자열 or None(통과)."""
    if not flow.current.verified:
        return (f"완료 거부: 이 Task({flow.current.task_id})를 run으로 한 번도 실행하지 않았습니다 "
                f"— 산출물을 run으로 실제 실행한 뒤 complete_task 하세요(허위 완료 금지).")
    return None

def _gate_owner_incomplete(flow):
    """[게이트] 턴 한도 미완 반환 — owner가 '턴 한도'로 미완 반환한 Task는 완료 불가. 같은 owner에게
    request(Work)로 '이어서' 재위임해 마저 끝내야 한다(허위완료→다음 Task churn·유실 차단). 이어가기는
    Redo 한도와 무관하게 계속 가능. 에러문자열 or None(통과)."""
    if flow.current.owner_incomplete:
        return (f"완료 거부: 이 Task의 담당자가 '턴 한도'로 작업을 미완 반환했습니다 — 새 Task로 넘어가지 말고, "
                f"같은 담당자에게 request(Work)로 '이어서 남은 부분을 마저 끝내라'고 재위임해 완성시킨 뒤 "
                f"complete_task 하세요(이어가기는 횟수 제한 없음). 미완을 두고 다음으로 넘어가면 그 작업이 유실됩니다.")
    return None

def _gate_owner_delivered(flow):
    """[게이트] owner 인도 전 대리 완료 차단 — owner에게 Work를 위임해 놓고(소유자 지정됨) 그 owner가
    '검증된 산출물+응답'을 아직 내지 않았는데 리더가 대신 완료하는 것을 막는다 — 이것이 사용자가 지적한
    '허위 완료'(owner가 일하는 중/응답 전인데 완료 때리고 다음 Task 열기)의 정확한 차단점. owner가 실제로
    일하고 응답이 돌아와야(owner_delivered) 완료 가능. (리더가 위임 없이 자기 도메인을 직접 한 Task는
    owner==0이라 이 게이트를 건너뛴다.) 에러문자열 or None(통과)."""
    if flow.current.owner and not flow.current.owner_delivered:
        return (f"완료 거부: 이 Task는 owner({flow.current.status.owner or flow.current.owner})에게 "
                f"위임돼 있는데 그 owner가 아직 '검증된 산출물'을 응답으로 내지 않았습니다(착수 전·작업 중일 "
                f"수 있음). **owner가 일하는 중에 대신 완료하지 마세요(허위 완료 금지).** 같은 owner에게 "
                f"request(Work)로 맡겨 run 검증 증거가 붙은 완료 응답을 받은 뒤 complete_task 하세요. "
                f"끝내 무응답이면 recruit/재배정으로 다른 담당에게 맡기세요(리더가 대리 구현·완료 금지).")
    return None

def _gate_percept(flow, args):
    """[게이트] 지각 비대칭 검증 — 범용 대문제 교정(사용자 "사운드 직군 없이 만들어냄" 2026-06-15). LLM의
    외부현실 검증(비전: 스크린샷→Read, WebSearch 대조)은 '검증자가 지각 가능한 차원'(시각·텍스트)을
    암묵 전제한다. 검증자가 직접 경험해야만 품질을 아는 차원(들어야 아는 소리·음악, 느껴야 아는
    손맛 등)은 외부 대조가 불가능해 'presence(코드가 호출되나)'로 회귀 → 비전문가의 코드 합성
    placeholder(라이브 P-010: 사운드=오실레이터 자급, 사운드 직군 0·recruit 0)가 완성으로 통과.
    지각 불가 차원은 '자기 판정'이 구조적으로 불가하므로, 자급을 완성으로 닫지 말고 '검증된 실제
    자원 또는 전문성'을 의무화한다(외부현실·전문화 원칙의 비시각 차원 확장). 흐름당 1회 보류 후
    재호출 통과(막지 않되 보이게 — 판단·범주는 리더. 직군·도메인 하드코딩 없음, 'gap_checked' 패턴).
    [지각 비대칭 — 실제 자원 통합 요구(2026-06-15 P-015 라이브 규명으로 재강화). 외부소싱(WebFetch)
    증거게이트는 '레퍼런스 읽기'를 '자원 통합'으로 오인해 합성 placeholder를 통과시켰다(라이브: 사운드=
    오실레이터 합성, 작업공간 에셋 파일 0인데 WebFetch 11회로 읽기만 함). 증거를 '외부 접촉'→'실제 제작
    자원 파일 존재'로 강화: 작업공간에 코드 아닌 실재 에셋(_has_real_asset)이 있거나, 그런 자원이 필요
    없음을 result 첫 줄 '[지각차원 없음]'으로 의식적 명시해야 통과. 읽기만으론 안 닫힌다. 도메인 중립
    (에셋=실재물 파일, 특정 장르/직군 아님), 명시 탈출구 상시(판단은 리더). 품질>과제한(사용자 승인).]
    통과 시 flow._gate_pass에 per-Task 키를 영속(_ckpt). 에러문자열 or None(통과)."""
    import re
    if not getattr(flow, "percept_checked", False) and ("percept", flow.current.task_id) not in flow._gate_pass:
        _res = args.get("result") or ""
        # 마커 [지각차원 없음/불가] — 뒤(같은 줄)에 사유가 있으면 'reasoned'(의식적), 없으면 'bare'(반사적).
        _pm = re.search(r"\[\s*지각차원\s*(?:없음|불가)\s*[:：]?\s*\]?[ \t]*([^\n]*)", _res)
        _pd_any = bool(_pm)
        _pd_reasoned = bool(_pm) and len(_pm.group(1).strip()) >= 2
        # [탐지→강제] 이 Task가 *오디오* 지각차원을 가졌나(팀 직군 OR 합의기준·원문) — 가졌으면 빈 '없음' 불가
        _p_labels = [flow._info(m) for m in flow.current.team] + [flow._info(flow.leader)]
        _p_texts = [flow.current.status.goal, flow.current.acceptance,
                    flow.current.standard, getattr(flow, "origin_request", "")]
        _p_essential = _perceptual_essential(_p_labels, _p_texts)
        _p_asset = _has_real_asset(getattr(flow, "workspace", None))
        # 통과: 실제 에셋 OR 사유 있는 명시. 비-essential이면 반사적 빈 '없음'도 허용(가벼운 탈출 유지).
        if not (_p_asset or _pd_reasoned or (_pd_any and not _p_essential)):
            if flow.log:
                flow.log("complete_percept_gate", task=flow.current.task_id,
                         essential=_p_essential)
            if _p_essential and _pd_any and not _pd_reasoned:
                return ("마감 보류(지각 비대칭 — 오디오 차원이 *있는데* 빈 '없음' 선언): 이 Task엔 "
                        "사운드/음악 전문가가 있거나 합의 기준·원문이 소리·음악 품질을 명시합니다 — 즉 이 작품엔 "
                        "'들어야 아는' 지각차원이 분명히 있어 `[지각차원 없음]`(빈 선언)은 모순이라 통과 안 됩니다. "
                        "통과하려면 둘 중 하나: ① **실제 음원·효과음 파일을 WebSearch로 찾아 다운로드(CC0 등)해 "
                        "작업공간에 통합**하세요(코드 오실레이터 합성 ✕ — LLM은 소리를 못 들어 합성 placeholder가 "
                        "'완성'으로 통과됨). 직접 못 받으면 그 전문가를 recruit해 받게 하세요. ② 정말 불필요·불가하면 "
                        "(예: 사운드 설정 UI뿐, 폐쇄망) result에 **'[지각차원 불가: <사유>]'** 또는 "
                        "**'[지각차원 없음: <왜 소리·음악이 본질이 아닌지>]'**를 *사유와 함께* 적어 재호출하세요 "
                        "(반사적 빈 태그 ✕ — 의식적 판단).")
            return ("마감 보류(지각 비대칭 — 실제 자원 필요, 읽기·합성으론 통과 안 됨): 이 작품이 만든 것 "
                    "중 **직접 경험해야만(보는 것 말고 듣거나 느껴야) 품질을 아는 차원**이 있습니까? — 있다면 "
                    "LLM 검증자는 지각할 수 없어 코드로 합성한 placeholder가 '완성'으로 통과됩니다. 통과하려면 "
                    "둘 중 하나: ① **실제 제작 자원(코드로 합성한 게 아니라 외부에서 받아온 실재 에셋 파일)을 "
                    "WebSearch로 찾아 다운로드(CC0 등)해 작업공간에 통합**하세요 — *읽기만으론 안 됩니다, 실제 "
                    "파일이 있어야 게이트가 열립니다*. 직접 못 받으면 그 분야 **전문가를 recruit**해 받게 "
                    "하세요. ② 그런 실재 자원이 필요 없으면(순수 코드 작품, 또는 손맛처럼 코드로 구현·교차검증되는 "
                    "차원) result **첫 줄에 '[지각차원 없음] <이유>'**를 적어 재호출하세요(의식적 판단). 무엇이 "
                    "그런 차원인지는 작품을 아는 당신이 판단합니다(시스템은 특정 범주·직군을 지정하지 않음).")
        flow._gate_pass.add(("percept", flow.current.task_id))   # 이 산출물(Task)의 지각검사 통과 — 다음 Task는 다시 검사(per-Task)
        _ckpt(flow)              # [통과 영속] 보류 반환 전에도 누적 통과를 저장 → 복구가 재서술 안 시킴
    return None

def _gate_visual(flow, args):
    """[게이트] 시각 검증 — percept 게이트의 *시각 평행판*(2026-07, 사용자 "시각 결과는 왜 안 본거야"). percept
    게이트는 '오디오는 못 들으니 실제 에셋 요구'인데, *시각은 스크린샷으로 검증 가능*이라 **가정**했다(위
    주석 2067). 그러나 LLM 검증 환경(헤드리스)은 WebGL/GPU 렌더가 실패해 스크린샷이 검게/빈 화면으로
    나온다(라이브 P-003: 렌더 실패로 검은 맵이 presence·로직 QA를 통과·마감). 즉 시각도 '자동 검증 불가'일
    수 있다. 웹 UI(사용자가 화면으로 보는 것)를 마감하려면 실제 렌더를 *눈으로 확인*했음을 명시하거나
    ('[시각 검증: 무엇이 보였나]'), 못 했으면 정직히 '[시각 미검증: 사유]'(사람 시각 확인으로 넘김)를
    적어야 한다 — presence만으론 못 닫는다. 반사적 빈 태그 차단(사유 ≥2자, percept·acceptance 동 패턴).
    도메인 중립(웹 UI=시각, 장르·직군 무관), 명시 탈출구 상시(판단은 리더). 흐름당 1회 보류.
    에러문자열 or None(통과)."""
    import re
    if not getattr(flow, "visual_checked", False) and ("visual", flow.current.task_id) not in flow._gate_pass:
        _vr = args.get("result") or ""
        _v_ok = bool(re.search(r"\[\s*시각\s*검증\s*[:：]\s*\S.{1,}", _vr))
        _v_none = bool(re.search(r"\[\s*시각\s*(?:미검증|불가|차원\s*없음)\s*[:：]\s*\S.{1,}", _vr))
        if _has_visual_runtime(getattr(flow, "workspace", None)) and not (_v_ok or _v_none):
            if flow.log:
                flow.log("complete_visual_gate", task=flow.current.task_id)
            return (
                "마감 보류(시각 검증 — 실제 렌더를 봤는가): 이 산출물은 사용자가 *화면으로 봅니다*. LLM QA는 "
                "presence·로직(요소 존재·무크래시)은 봐도 **실제 렌더 결과**는 놓칠 수 있습니다 — 특히 "
                "WebGL/GPU는 헤드리스에서 렌더가 실패해 스크린샷이 검게 나옵니다(라이브 P-003: 렌더 실패로 검은 "
                "화면이 QA를 통과·마감). 통과하려면 둘 중 하나: ① 실제 렌더를 **스크린샷→눈으로 확인**하고 "
                "result에 '[시각 검증: 맵·캐릭터·UI가 실제로 어떻게 보였나]'(빈 'ok' ✕ — 무엇이 보였는지), "
                "또는 ② 못 봤으면(헤드리스 렌더 불가 등) '[시각 미검증: <사유>]'로 정직히 명시하세요(사람 "
                "시각 확인으로 넘어감). presence·'요소 존재'만으로 시각을 닫지 마세요.")
        flow._gate_pass.add(("visual", flow.current.task_id))
        _ckpt(flow)
    return None

def _acceptance_covered_by_e2e(flow, acc: str) -> int:
    """수용 기준의 각 줄이 **Task 경계 e2e 분모에 그대로 들어가 증거와 함께 통과**했으면 그 수를 반환.

    [2026-07-27, U-067 실측] 수용 관문은 '합의한 좋음 기준이 산출물에 도달했나'를 봇이 하나씩
    대조하라고 요구한다 — 봇이 반사적으로 도장 찍던 것을 막으려고 생긴 옳은 관문이다. 그런데 이
    판에서는 그 대조를 **구조가 이미 했다**: 수용 기준 두 줄이 각각 e2e 분모 항목이 되어 exact
    command로 실행되고 SYS receipt로 봉인돼 통과했다(condition:3·4). 그런데도 관문이 그 사실을
    못 봐, 0결함으로 통과한 판이 마감 앞에서 19번 재개하며 멎었다.

    측정이 대리를 이긴다 — 단, **e2e가 덮지 않은 항목이 하나라도 있으면 0을 반환**해 관문이 그대로
    봇에게 대조를 요구한다(도장 찍기 방지라는 원래 목적은 그 항목들에서 온전히 살아 있다).
    """
    if str(((getattr(flow, "wrapup_state", None) or {}).get("verdict") or "")) != "e2e_pass":
        return 0
    checklist = getattr(flow, "e2e_checklist", None) or []
    results = getattr(flow, "e2e_results", None) or {}
    if not checklist or not results:
        return 0
    import re as _re_a

    def _norm(t):
        return _re_a.sub(r"\s+", " ", str(t or "")).strip()

    passed_specs = []
    for item in checklist:
        if not isinstance(item, dict):
            return 0
        row = results.get(str(item.get("id") or ""))
        if not isinstance(row, dict) or not row.get("ok") or not str(row.get("evidence") or "").strip():
            continue
        # 항목의 '무엇을 본다'(spec)와 '어떻게 본다'(verifier_spec)를 **둘 다** 대조 대상에 넣는다 —
        # 수용 기준 줄은 조건문이므로 spec 쪽과 만난다(verifier_spec만 보면 실증 절차와 비교하게 된다).
        for _f in ("spec", "verifier_spec"):
            _v = _norm(item.get(_f))
            if _v:
                passed_specs.append(_v)
    if not passed_specs:
        return 0

    covered = 0
    for line in str(acc or "").splitlines():
        head = _norm(line.lstrip("-· ").split("|")[0])
        if len(head) < 12:                     # 너무 짧은 줄은 우연 일치 위험 — 대조로 치지 않는다
            continue
        if not any(head in spec for spec in passed_specs):
            return 0                           # 한 줄이라도 안 덮이면 관문은 그대로 봇에게
        covered += 1
    return covered


def _gate_acceptance(flow, args):
    """[게이트] 수용 계약 마감 바인딩 — 회의 전문성이 '코드'에 도달했는가(2026-06-15 P-015 규명). verified·percept·
    contrib·cross-check는 각각 '실행됨/실재 에셋/잠수 직군 실작업/홀리스틱 좋음'을 보지만, **회의에서
    합의한 구체 약속**(히트스톱·콤보·레이어드BGM 등)이 실제 산출물에 들어갔는지는 어느 게이트도 안 본다 —
    잠수 아닌 직군이 '뭔가'만 하면(사운드가 app.js에 6줄) contrib는 통과, cross-check는 '좋아?'로
    satisfice된다. 라이브: 회의 제안 6개 중 코드 반영 0인데 마감(사용자 "플레이하면 감이 없다"). 수용
    계약을 마감에 구속한다 — 각 항목 충족 증거(result '[수용기준 검증]' + 항목별 충족·증거) 또는 의식적
    드롭. 반사적 재호출로는 통과 안 됨(percept·contrib와 동 원리, 증거/명시 통과형). 도메인 중립(기준은
    팀이 회의에서 자작), 자율 보존(의식적 드롭/N·A 상시 — 판단은 리더). 흐름당 1회.
    에러문자열 or None(통과)."""
    import re
    from .._util import _speech_clip
    # [회귀 재검출 — 버전 인식(2026-07-08)] 교차검증 게이트가 last_verify_writes로 '코드 바뀌면 peer
    # 재검증'을 강제하듯, 수용계약 통과도 *그 버전 것*이다 — 통과 후 산출물이 바뀌면(writes_by_role 합
    # 증가) 이번 수정이 이미 통과한 기준을 깼을 수 있으므로 캐시된 통과를 무효화해 재검증한다. 종전엔
    # _gate_pass가 task당 1회('오락가락 차단')라, 3라운드에서 통과한 뒤 7라운드 반쪽수정이 기준을 깨도
    # '이미 통과'로 눈감았다(오실레이션이 마감으로 새던 구멍). 이게 12회 회로차단기(매직넘버)가 존재하던
    # 이유 — 수렴을 '카운트'로 포기하는 대신 '현재 버전 전 기준 통과'로 구조 강제. 안 바뀌면 통과 유지.
    _cur_w = sum(int(v) for v in (flow.writes_by_role or {}).values())
    _apw = getattr(flow.current, "acceptance_pass_writes", -1)
    if (("acceptance", flow.current.task_id) in flow._gate_pass
            and _apw >= 0 and _cur_w > _apw):
        flow._gate_pass.discard(("acceptance", flow.current.task_id))
        if flow.log:
            flow.log("acceptance_reverify_on_change", task=flow.current.task_id, at=_apw, now=_cur_w)
    if not getattr(flow, "acceptance_checked", False) and ("acceptance", flow.current.task_id) not in flow._gate_pass:
        _result = args.get("result") or ""
        # [반사적 빈 탈출 차단 — percept와 동 원리(2026-06-19 감사)] '검증/충족/확인/반영'은 항목별
        # 증거가 *뒤따르는* 헤더(다음 줄에 회계 — 같은 줄 강제 불가)지만, '해당없음' 탈출(N/A·없음·
        # 면제·불필요)이 사유 없는 빈 마커만으로 통과하면 satisfice다 — 게이트 본문이 명시한 '반사적
        # 재호출로는 통과 안 됨'과 모순. 그래서 탈출 마커는 *사유(≥2자)* 를 요구한다(데이터출처·percept와
        # 같은 증거/명시 패턴). 헤더는 그대로(뒤따르는 항목 회계가 증거), 탈출만 사유 강제. 무한 반려 아님.
        _acc_hdr = bool(re.search(r"\[\s*수용\s*기준\s*(?:검증|충족|확인|반영)\s*\]", _result, re.I))
        _acc_escape = bool(re.search(
            r"\[\s*수용\s*기준\s*(?:N\s*/?\s*A|없음|면제|불필요)\s*[:：]?\s*\]?[ \t]*\S{2,}", _result, re.I))
        _accounted = _acc_hdr or _acc_escape
        acc = (flow.current.acceptance or "").strip()
        if acc and not _accounted:
            _cov = _acceptance_covered_by_e2e(flow, acc)
            if _cov:
                _accounted = True
                if flow.log:
                    flow.log("acceptance_covered_by_e2e", task=flow.current.task_id, items=_cov)
        if not _accounted:
            if flow.log:
                flow.log("acceptance_gate", task=flow.current.task_id, defined=bool(acc))
            if acc:
                return (
                    "마감 보류(수용 계약 검증 — 합의한 '좋음' 기준이 코드에 도달했나. 반사적 재호출로는 통과 "
                    "안 됨): 팀이 회의에서 합의한 수용 기준입니다 —\n" + _speech_clip(acc, 1500) + "\n\n각 항목이 "
                    "**실제 산출물에 들어가 검증됐는지** 직접 확인하세요(회의에서 '중요하다'고 한 게 코드에 "
                    "없으면 그게 '플레이하면 감이 없다'의 정확한 원인입니다 — 라이브 P-015: 제안 6개 중 0개 "
                    "반영). 미구현 항목은 그 도메인 owner에게 request(Work)로 맡겨 **실제로 넣고 교차검증까지** "
                    "받으세요. 그런 뒤 result에 **'[수용기준 검증]'** 헤더와 **항목별로 (충족 — 어떻게 "
                    "확인했나 / [드롭] <왜 뺐나>)**를 적어 재호출하세요(의식적으로 뺀 항목은 드롭으로 남음 — "
                    "판단은 당신).")
            return (
                "마감 보류(수용 계약 미정의 — '좋음'의 구체 기준이 없음): 이 산출물이 '작동'을 넘어 '좋다"
                "(상용 수준)'가 되려면 참이어야 할 **구체·검증가능한 조건**이 무엇입니까? — 같은 종류의 "
                "**훌륭한 예와 대조**해 그것이 당연히 갖춘, '감'을 만드는 요소들(작품 종류를 아는 당신이 판단)을 "
                "기준으로 삼으세요. 회의를 했다면 거기서 전문가들이 제안한 구체 항목이 곧 기준입니다. "
                "set_goal(acceptance=…)로 박아 두거나, 지금 result에 **'[수용기준 검증]'** 헤더로 항목별 충족 "
                "증거를 적어 재호출하세요. 정말 품질 기준이랄 게 없는 단순 산출물이면 result에 **'[수용기준 "
                "N/A] <이유>'**를 적어 재호출하세요(의식적 판단 — 그냥 재호출론 통과 안 됨).")
        flow._gate_pass.add(("acceptance", flow.current.task_id))   # 이 산출물(Task)의 수용계약 검사 통과(per-Task)
        flow.current.acceptance_pass_writes = _cur_w   # [버전 인식] 이 통과가 유효한 산출물 버전(저작수) 각인 → 이후 변경 시 재검증
        _ckpt(flow)              # [통과 영속] 보류 반환 전에도 누적 통과를 저장 → 복구가 재서술 안 시킴
    return None

def _gate_deploy_fresh(flow, args):
    """[게이트] 배포 신선도 — 검증한 산출물과 라이브가 같은 버전이어야 마감(2026-07-08, 사용자 규명:
    '로컬을 서버에 적용 안 했을 뿐인데 재작성하려 했다'). 근본: 이전 흐름이 '로컬 수정·검증 완료, 라이브
    미배포'인 채 마감돼도 아무것도 안 막아 로컬≠라이브 분기가 다음 흐름까지 생존 — 다음 팀이 라이브(옛
    버전)를 '현재 코드'로 앵커해 옛 결함을 오진, 이미 고친 걸 재작성했다(라이브 P-005 msg201). 상태 기반
    (배포하면 저작수가 갱신돼 자연 통과 — 카운트·키워드·flow-once 없음): 배포 이력이 있는 흐름에서 마지막
    배포 이후 로컬 변경이 있으면 보류. 비-라이브 산출물은 result '[배포 불필요: 사유]'로 의식적 탈출.
    에러문자열 or None(통과)."""
    import re
    if not getattr(flow, "_deployed_once", False):
        return None                                     # 배포 이력 없는 흐름(로컬 산출물) — 무발동
    if getattr(flow, "deploy_capped", False):
        return None   # [게이트↔캡 데드락 차단] 캡이 배포를 막은 흐름에 '배포하고 와라'를 요구하면 상호 교착 —
                      # 캡 경로가 자기 처방('배포 구조 문제를 정직 보고하고 마감')을 소유하므로 이 게이트는 비킴.
    _dw = getattr(flow, "_deploy_writes", -1)
    if _dw < 0:
        return None
    _cw = sum(int(v) for v in (flow.writes_by_role or {}).values())
    if _cw <= _dw:
        return None                                     # 라이브 = 로컬 최신 — 통과
    if re.search(r"\[\s*배포\s*불필요\s*[:：]\s*\S{2,}", args.get("result") or ""):
        return None                                     # 의식적 드롭(비-라이브 변경 — 사유 필수)
    if flow.log:
        flow.log("deploy_fresh_gate", task=flow.current.task_id, at=_dw, now=_cw)
    return (f"마감 보류(배포 신선도 — 검증한 버전 ≠ 라이브): 마지막 배포 이후 로컬 변경 {_cw - _dw}건이 "
            f"**라이브에 미반영**입니다. 이 상태로 마감하면 다음 흐름이 라이브(옛 버전)를 현재로 오인해 "
            f"이미 고친 결함을 재작성합니다(실제 발생). **deploy로 최신을 배포하고 라이브를 확인한 뒤** "
            f"재호출하세요. 이 변경이 정말 라이브 산출물과 무관하면(문서·로컬 전용) result에 "
            f"**'[배포 불필요: <사유>]'**를 적어 재호출하세요(사유 필수 — 그냥 재호출론 통과 안 됨).")


def _gate_existence(flow, args):
    """[게이트] G5 — 존재이유 회계(B-05). acceptance에 '[존재이유]' 테스트가 박힌 Task는 마감 result가 그 **실행
    회계를 별도로** 담아야 한다 — 수용기준 헤더 하나에 묻히면 부품 체크로 satisfice된다(존재이유는
    전체·부정형 검증이라 따로 요구, 설계문서 G5). 증거/명시 통과형(acceptance 게이트와 동 원리),
    per-Task 키잉. 테스트가 없는 Task(마커 부재)는 무발동. existence_checked는 테스트 우회 플래그.
    에러문자열 or None(통과)."""
    import re
    from .._util import _speech_clip
    if (not getattr(flow, "existence_checked", False)
            and ("existence", flow.current.task_id) not in flow._gate_pass
            and re.search(r"\[\s*존재\s*이유\s*[\]:：]", flow.current.acceptance or "")):
        _res_e = args.get("result") or ""
        _e_ok = bool(re.search(r"\[\s*존재\s*이유\s*(?:검증|충족|확인|통과)\s*[\]:：]", _res_e))
        _e_na = bool(re.search(r"\[\s*존재\s*이유\s*(?:N\s*/?\s*A|불가|면제)\s*[:：]\s*\S{2,}", _res_e, re.I))
        # [구조가 이미 한 실행은 인정한다(2026-07-27, 전수감사)] 이 관문은 봇이 쓴 텍스트로만 열려
        # 있었다 — 구조 드라이버가 채울 인자조차 없어, 봇이 안 쓰면 **영영 안 열린다**. 그런데
        # 존재이유 줄이 Task 경계 e2e 분모에 그대로 들어가 exact command로 실행되고 SYS receipt로
        # 봉인돼 통과했다면 그 '전체 실행'은 이미 일어난 것이다(수용 관문과 같은 원리·같은
        # one-strike 규칙 — 한 줄이라도 안 덮이면 인정하지 않는다).
        if not (_e_ok or _e_na):
            _exi_only = "\n".join(ln for ln in (flow.current.acceptance or "").splitlines()
                                   if "존재이유" in ln)
            if _acceptance_covered_by_e2e(flow, _exi_only):
                _e_ok = True
                if flow.log:
                    flow.log("existence_covered_by_e2e", task=flow.current.task_id)
        if not (_e_ok or _e_na):
            if flow.log:
                flow.log("existence_gate", task=flow.current.task_id)
            _exi_lines = "\n".join(ln for ln in (flow.current.acceptance or "").splitlines()
                                   if "존재이유" in ln)
            return (
                "마감 보류(존재이유 테스트 — 실제 실행 회계 필요, 재호출만으론 통과 안 됨): 이 Task의 "
                "acceptance엔 팀이 박은 **존재이유 테스트**(전체·부정형 검증 — 실패하면 핵심 목적이 깨지는 "
                "것)가 있습니다:\n" + _speech_clip(_exi_lines, 600) + "\n\n마감 전 이 테스트를 **최종 사용자처럼 "
                "end-to-end로 실제 실행**해 통과를 확인하고, result에 **'[존재이유 검증]' 헤더 + 무엇을 어떻게 "
                "실행해 어떤 결과가 나왔는지**를 별도로 적어 재호출하세요 — 부품이 *있는지*가 아니라 *전체가 "
                "목적을 달성하는지*(부정형 테스트가 실제로 실패를 막는지)를 봅니다. 정말 부적용이면 "
                "**'[존재이유 N/A: <사유>]'**(사유 필수 — 빈 태그·그냥 재호출은 통과 안 됨).")
        flow._gate_pass.add(("existence", flow.current.task_id))   # 이 산출물(Task)의 존재이유 회계 통과(per-Task)
        _ckpt(flow)
    return None

def _gate_data_provenance(flow, args):
    """[게이트] 데이터 출처 — 합성/하드코딩 데이터를 '공공·실데이터 학습'으로 위장 차단(2026-06-18,
    라이브 P-021). percept(합성 에셋)·acceptance(합의 약속)와 평행. 요청이 real/public 데이터
    학습을 요구하는데 모델이 *지어낸* 데이터로 학습됐고(코드에 합성 표식) 작업공간에 실제 데이터
    파일·출처 증거가 없으면 보류 — 실데이터를 받아 재학습하거나, 정말 불가하면 result 첫머리에
    의식적으로 명시. 도메인·직군 하드코딩 없음(요청 의도로만 발동), 흐름당 1회(percept와 동 패턴).
    에러문자열 or None(통과)."""
    import re
    if not getattr(flow, "data_prov_checked", False) and ("data_prov", flow.current.task_id) not in flow._gate_pass:
        _st = flow.current.status if getattr(flow.current, "status", None) else None
        _intent = " ".join([str(getattr(flow, "origin_request", "") or ""),
                             str((_st.purpose if _st else "") or ""),
                             str((_st.goal if _st else "") or "")])
        if _wants_real_data(_intent):
            _res = args.get("result") or ""
            _declared = bool(re.search(r"\[\s*데이터\s*출처\s*[:：\]]", _res)) or \
                bool(re.search(r"\[\s*합성\s*(?:불가피|허용|의도|데이터\s*명시)", _res))
            _synth = _synthesizes_data(getattr(flow, "workspace", None))
            if _synth and not _has_real_dataset(getattr(flow, "workspace", None)) and not _declared:
                if flow.log:
                    flow.log("data_provenance_gate", task=flow.current.task_id,
                             file=_synth[0], marker=_synth[1])
                return (
                    "마감 보류(데이터 출처 — '실제·공공 데이터로 학습'인데 데이터를 지어냈습니다): 사용자 "
                    f"요청이 실제/공공 데이터 학습을 요구하는데, `{_synth[0]}`이(가) 데이터를 **합성/하드코딩**"
                    f"으로 만들고(표식 '{_synth[1]}') 작업공간에 받아온 실제 데이터 파일(csv/parquet 등)이 "
                    "없습니다 — 모델이 배운 건 자기가 지어낸 분포라 'MAE 성공'도 순환논리입니다(요구 위반). "
                    "통과하려면 둘 중 하나: ① 그 데이터 owner가 **실제 데이터를 받아**(공공데이터의 무키 "
                    "벌크 다운로드·공개 데이터셋 미러를 WebSearch로 찾아 curl/WebFetch) 작업공간에 두고 "
                    "그것으로 재학습하세요 — *합성으로 때우지 말 것*. ② 실제 데이터가 정말 닿지 않으면(키 "
                    "필수·폐쇄망) result 첫 줄에 **'[데이터 출처: <실제 출처 또는 왜 불가한지>]'**를 적어 "
                    "의식적으로 명시하고 재호출하세요(가짜를 진짜인 척 닫지 말 것 — 그냥 재호출론 통과 안 됨).")
        flow._gate_pass.add(("data_prov", flow.current.task_id))   # 이 산출물(Task)의 데이터출처 검사 통과(per-Task)
        _ckpt(flow)              # [통과 영속] 보류 반환 전에도 누적 통과를 저장 → 복구가 재서술 안 시킴
    return None

def _gate_cross_check(flow, third, has_product, _engx, _scopex):
    """[게이트] 검증 분업/교차 검증 의무 — 품질 판정이 리더 1인에게 독점되는 것을 구조적으로 흔든다(라이브
    P-009: QA·교차 검증 0인 채 단독 마감 → 브라우저 렉·적 돌진 등 사용성 결함이 그대로 통과,
    사용자가 첫 발견). owner 인도 후 '다른 멤버'의 검증 참여가 0이면 첫 호출만 보류하고 검증
    위임을 안내한다 — 재호출은 통과(판단은 결국 리더 몫, 무한 반려 금지. 직군 키워드 없음).
    [교차 검증 의무 — Rule/Task.md 6 (사용자 확정: 범용 이치는 하드 제한도 옳다)]
    작업자(owner) 아닌 멤버가 산출물을 '사용자처럼 실제로 사용해' 검증해야 완수 선언 가능.
    제3멤버가 팀에 있는 한 우회 없음(거부 반복) — 라이브 P-009: 단독 마감이 렉·사용성
    결함을 통과시킴. 제3멤버가 정말 없을 때만 예외(단독 마감 마커가 기록에 남는다).
    [독립 검증 = 다른 도메인 (동질 모델 원리)] 검증은 '다른 관점'이라야 결함을 본다 — 같은 Claude·
    같은 직군 검증자는 같은 맹점(에코)이라 독립이 아니다. owner와 도메인 다른, 지금 도달 가능한
    검증자가 있으면 그 독립 검증을 요구하고, 그런 동료가 없거나 전원 타 흐름 점유면 같은 직군 검증으로
    폴백(단일도메인 팀·교착 방지). cross-check 자체(≥1 타멤버)는 종전대로 하드 의무.
    에러문자열 or None(통과)."""
    from .._util import _speech_clip
    from .communication import _jobs_of, _norm_job
    _ownx = flow.current.owner or flow.leader
    _odx = {_norm_job(j) for j in _jobs_of(flow._info(_ownx) or "")} - {""}
    def _offdom_reach(m):
        md = {_norm_job(j) for j in _jobs_of(flow._info(m) or "")} - {""}
        if not (_odx and md and not (md & _odx)):
            return False
        return not (_engx is not None and _scopex is not None and _engx.busy_elsewhere(m, _scopex))
    third_offdom = [m for m in third if _offdom_reach(m)]
    cc_ok = (flow.current.cross_check_offdomain > 0
             or (flow.current.cross_checks > 0 and not third_offdom))
    if has_product and not cc_ok and third:
        # [반-스래싱 — 리더 독점 차단(2026-06-20 P-024 규명: 리더가 같은 Task를 7회 재마감 + run 98회
        # 자가검증)] 교차검증 게이트가 보류될 때마다 카운트해, 3회+면 '혼자 떠안고 헛돎' 경보로 에스컬레이트.
        # 리더의 run은 교차검증으로 안 쳐주므로(peer 필수) 결과 문구만 바꿔 재호출하면 영원히 막힌다 —
        # "멈추고 검증 1회 위임하라"를 하드 문구로. cross_check가 오르면 cc_ok=True로 자연 통과(교착 0).
        flow.current.cc_held = getattr(flow.current, "cc_held", 0) + 1
        # [안내로 안 끊기는 자리는 구조로 끊는다(2026-08-03, 실측 U-478)] 이 게이트는 "다른 멤버의
        # 검증 응답 1건"을 요구하면서 그 응답을 **받아내는 주체를 두지 않는다** — 리더의 자발성에
        # 맡긴다. SYS는 e2e는 직접 구동하는데(_drive_task_boundary_e2e) 여기는 안 한다.
        # 실측 U-478: 13:49 e2e 통과(결함 0) 뒤 13:50 이 게이트가 막았고, 지목된 검증자는 14:52에
        # 검증 대신 재위임했으며, 리더는 마감을 반복 호출해 holds 5까지 갔다(한 시간 14턴 $5.57,
        # 판은 그 사이 한 번 정지). 게이트가 "재호출하지 말라"고 하드 문구로 말해도 반복된다.
        # 여기서 깨울 수는 없으므로(동기 함수) 누구를 깨워야 하는지 신호만 남긴다 — 소비는 SYS 루프.
        try:
            if flow.current.cc_held >= 3:
                flow._close_cc_retry = True     # 소비는 SYS 마감 구동부(다른 동료에게 재발송)
        except Exception:
            pass
        _thrash = ""
        if flow.current.cc_held >= 3:
            if flow.log:
                flow.log("complete_thrash", task=flow.current.task_id, holds=flow.current.cc_held)
            _thrash = (f"\n\n⚠ [반복 마감 {flow.current.cc_held}회 — 독점·헛돎 경보] 같은 Task를 계속 "
                       f"마감 시도하는데 **다른 멤버의 검증이 여전히 0**입니다. 결과 문구만 바꿔 재호출하면 "
                       f"*영원히* 막힙니다 — **리더가 run으로 혼자 반복 검증하는 건 교차검증으로 인정 안 됩니다.** "
                       f"지금 **멈추고 딱 하나**: 위에 지목된 검증자 1명에게 request(Work)로 '실제로 실행·사용해 "
                       f"검증'을 맡기고 **그 응답이 올 때까지 complete_task를 다시 부르지 마세요**(응답이 오면 "
                       f"게이트가 자동으로 열립니다). 전체를 혼자 떠안지 말 것 — 그게 '리더 독점'입니다.")
        idle = [m for m in third if flow.act_by.get(m, 0) == 0]
        idle_note = (f"\n[정보] 이 Task 팀에서 **실작업·검증 참여 0**인 멤버: {flow._names(idle)} — "
                     f"goal에 이들의 전문 영역이 있다면 그 부분의 검증·보완을 이들에게 맡기는 것이 "
                     f"자연스럽습니다." if idle else "")
        per_item = ("goal의 각 부분이 '존재하나'가 아니라 **그 산출물을 쓰는 사람으로서 처음부터 끝까지 "
                    "써본 경험**으로 — **시각 산출물이면 화면을 스크린샷으로 찍어 Read로 직접 '눈으로 보고'**"
                    "(존재가 아니라 '실제로 이렇게 보인다/작동한다'를 직접 확인 — 임시방편처럼 보이는지 상용 "
                    "수준인지), **같은 종류의 훌륭한 예를 WebSearch로 실제로 찾아 대조**해 '써보니 좋은가·"
                    "답답한가? 뭐가 싸구려 같고 빠지거나 떨어지나? 상용 수준이면 당연히 있을 게 없나?'를")
        # [RFC-008 P0 + RFC-010 P4 — 직무 기준을 '좋은가' 비평 루브릭으로] 산출물 도메인의 craft
        # profile을 검증 루브릭으로 제공한다. 검증자가 "작동하는가"(holistic)가 아니라 "**실제로
        # 써보니 이 도메인 기준에 비춰 좋은가·뭐가 아쉬운가**"를 차원별로 보게 — rubric-guided judge가
        # 인간 일치 2배(LLM-Rubric 2501.00274). 단일 점수 아닌 차원별 비평 + 사용자가 최종 취향 앵커.
        rubric = ""
        owner_job = ((flow._info(flow.current.owner) if flow.current.owner
                      else flow._info(flow.leader)) or "").strip()
        if callable(getattr(flow, "craft_of", None)) and owner_job:
            parts = [flow.craft_of(j) for j in owner_job.split("·") if j.strip()]
            parts = [p for p in parts if p]
            if parts:
                rubric = (f"\n[검증 루브릭 — 산출물 도메인 '{owner_job}'의 품질 기준. 검증 위임 본문에 "
                          f"**이 기준을 그대로 전달**하고, 검증자가 **실제로 실행/플레이한 뒤** 각 항목을 "
                          f"'써보니 좋은가·충분한가'로 평가하게 하세요 — '돌아가는가'가 아니라 '이 기준에 "
                          f"비춰 좋은가, 뭐가 아쉬운가'가 질문입니다(미달은 구체적 결함으로):\n"
                          + _speech_clip("\n---\n".join(parts), 2500) + "]")
        fb_v = [f.get("text", "") for f in (getattr(flow, "user_feedback", None) or []) if f.get("text")]
        taste_v = ""
        if fb_v:
            taste_v = ("\n[사용자 표준 — 이 사용자가 *여러 작업에 걸쳐* 반복 요구한 것(크로스-프로젝트)] "
                       "이 작품뿐 아니라 *과거 프로젝트들*에서도 사용자가 해 온 비평입니다 — *유일하게 믿을 수 "
                       "있는 품질 앵커*(같은 모델 taste는 천장이라). 검증자에게 **이 표준들이 이번 산출물엔 "
                       "처음부터 반영됐는지 직접 써보고 확인**하라 전하세요(한 작품서 고친 걸 또 틀리지 말 것 — "
                       "되풀이된 불만이 곧 이 사용자의 상용 기준):\n"
                       + "\n".join(f"  · “{_speech_clip(t, 140)}”" for t in fb_v[:10]))
        # [수용 계약을 검증 기준으로] 교차 검증을 '좋아?'(satisfice되는 홀리스틱)가 아니라 팀이 합의한
        # 구체 기준 대조로 — 검증자가 각 항목의 실제 충족을 써보고 확인하게(회의 전문성→검증 루프 연결).
        acc_x = (flow.current.acceptance or "").strip()
        acc_v = ("\n[수용 계약 — 이 항목들이 실제로 충족됐는지 검증] 팀이 합의한 '좋음' 기준입니다. 검증자에게 "
                 "**각 항목이 산출물에 실제로 들어가 작동하는지 써보고 확인**하라고 전하세요(존재가 아니라 "
                 "'이 기준대로 됐나'):\n" + _speech_clip(acc_x, 1200)) if acc_x else ""
        std_x = (flow.current.standard or "").strip()
        standard_v = ("\n[최대성 기준 — *구성요소별 분해 대조* (PHASE 3, 외부 grounding)] 이 작품의 *최대판 "
                      "구성요소(분해)*입니다(목표 단계서 실제 exemplar에 앵커해 박은 부품 목록). 검증자(다른 "
                      "도메인·신선한 시각)에게: '좋아?'(홀리스틱이라 satisfice됨)를 묻지 말고 — **위 *각 "
                      "구성요소를 하나씩* 워크스페이스 ls/read로 실측**해 (요소: 있나? / 최대만큼 깊나? / "
                      "빠졌나?)를 *항목별*로 보고하게 하세요(구성적 품질은 *보지 않아도* 구성요소로 판정 "
                      "가능 — taste 아님, 천장은 좁은 지각 잔여뿐). **그리고 *기능 점검*에 그치지 말고 "
                      "검증자가 *진짜 사용자처럼 주 사용 흐름을 직접 걸어보게* 하세요 — '핵심 목표(예: 내 위치 "
                      "결과 알기)를 *몇 단계*에 달성하나? 자동(위치감지·기본값·원탭)인가 *수동 다단계 폼*인가? "
                      "최고 앱의 흐름과 대조해 마찰이 어디서 나나?'(라이브 갭: 위치기반인데 자동위치 0·수동 "
                      "select 강제 = 기능완비여도 실사용 실패). 접근성(라벨·키보드)도 함께. 못 미치거나 빠진 "
                      "요소·마찰은 그 도메인 owner에게 개선 위임 → 재검증(갭이 닫힐 때까지 — 이게 깊이의 반복, "
                      "0-redo를 깨는 지점). 정말 불필요한 요소만 result에 사유 영속(의식적 N/A — 객관 거짓주장 "
                      "불가):\n" + _speech_clip(std_x, 1500)) if std_x else ""
        iface_x = (flow.current.interfaces or "").strip()
        iface_v = ("\n[통합 검증 — 인터페이스 계약 준수 (PHASE 1.3 / L2)] 도메인 간 계약입니다. 검증자에게 — "
                   "이 계약이 *실제로 지켜지나*(예: 프론트가 백 포맷을 진짜 소비? VFX-사운드 타이밍 싱크?)를 "
                   "워크스페이스 실측으로 확인하게 하세요('각자 만들고 안 붙는' 사일로 차단):\n"
                   + _speech_clip(iface_x, 800)) if iface_x else ""
        _ver_state = ("**다른 멤버의 검증 참여가 0**입니다" if flow.current.cross_checks == 0
                      else "검증이 **같은 직군(에코)뿐**이라 독립 검증이 없습니다(같은 관점=같은 맹점)")
        indep_note = (f"\n[독립 검증 — 다른 도메인 필수] owner와 **다른 도메인** 동료가 검증해야 독립적입니다"
                      f"(같은 직군 검증은 에코라 같은 결함을 못 봄). 지금 가능한 독립 검증자: "
                      f"{flow._names(third_offdom)}." if third_offdom else "")
        # [검증 역할(QA) 우대 — 최종·전체 인수(2026-06-19 사용자 설계)] 팀에 '검증/품질' 기능 역할이 있으면
        # 전체·사용자관점 검증은 그 역할이 특화돼 있으니 우선 라우팅한다(만든 사람의 저자편향 밖 = 최종 측정).
        # 부분·기술 검증은 도메인 동료도 OK — QA는 '완성품 전체를 사용자처럼' 보는 데 우대. 타이틀이 아니라 기능.
        _verifiers = [m for m in third if _is_verifier(flow._info(m))]
        qa_note = (f"\n[검증 역할 우대 — 전체·최종 인수는 QA에게] 이 팀에 **검증 전문 역할**이 있습니다: "
                   f"{flow._names(_verifiers)} — 부분 검증은 도메인 동료도 되지만, **완성품 전체를 사용자처럼 "
                   f"처음부터 끝까지 써보는 '최종 인수'는 이 역할에게 우선 맡기세요**(만든 사람·도메인 전문가는 "
                   f"자기 부분만 보고 저자편향이 있어 전체 사용자경험을 객관적으로 못 봅니다 — 그래서 QA가 전담). "
                   f"이들이 그 Task 멤버가 아니면 create_task members에 넣거나 request(Work)로 검증을 위임하세요."
                   if _verifiers else "")
        # [수평 수렴 — '단방향 검증 1회'를 'meet 교차비평+peer→owner 보완'으로 (2026-06-19 사용자 설계:
        # "사람 많은데 대화 적음")] cross-check 게이트는 종전 '검증자→리더 1회 보고'면 충족돼, 빌드가
        # '검증자 1명이 읽고 끝'인 얕은 파이프라인이 되곤 했다(P-023: 참여자 많고 대화 1회). 게이트 바닥은
        # 그대로 두되(교착 위험 없이), 인도 후 동료들을 meet로 다시 모아 '합쳐 놓고 좋은가'를 *수평으로*
        # 교차비평하고 비평자가 owner에게 직접 보완을 넘기도록 안내한다 — peer→owner는 다른 위임쌍이라
        # 리더 Redo 예산도 안 쓰고(프로토콜이 이미 선호하는 경로), 1라운드는 '반복'이 아니라 정상 수렴이다.
        _horiz_note = ("\n[수평 수렴 — 단방향 검증보다 meet 교차비평] 검증을 '검증자→리더 1회 보고'로 받고 "
                       "끝내지 말고, **owner와 독립 검증자(있으면 QA)를 meet로 함께 다시 모아** '합쳐 놓고 보니 "
                       "좋은가'를 서로 교차비평하게 한 뒤, **비평한 검증자가 그 owner에게 직접 request(Work)로** "
                       "개선점을 넘겨 owner가 1회 끌어올리게 하세요(수평 수렴; 리더 허브 우회 — peer→owner 보완은 "
                       "리더 Redo 예산도 안 씁니다). 품질 근거를 든 이 1라운드는 '결함 없는 반복'이 아니라 정상 "
                       "수렴입니다(금지되는 건 기준 없는 반사적 반복).")
        return (f"완료 거부(교차 검증 의무 — Rule/Task + RFC-010 P1·P2 / RFC-011 M2): 산출물 인도 후 "
                f"{_ver_state}. **만든 사람이 아닌** 다른 멤버에게 request(Work)로 "
                f"'**코드만 읽지 말고 산출물을 처음부터 끝까지 실제로 실행·사용·플레이해 본 뒤**(라이브 "
                f"근거: 실제로 써본 검증자가 읽기만 한 쪽보다 결함을 훨씬 많이 잡음 — TITAN 82% vs 18%) "
                f"{per_item} 보고하라'고 맡긴 뒤 마감하세요. **'요소 존재·JS 에러 0·서버 기동됨' 같은 것은 "
                f"'작동'이지 '좋음'의 증거가 아닙니다 — 검증으로 인정하지 마세요**(라이브: 그렇게 통과시킨 게 "
                f"'상용 수준 아님'으로 반려됨). 검증자의 결함·아쉬움 보고가 Redo(창의적 개선)의 근거입니다. "
                f"**자기 산출물 자기검증은 무효**(편향 — Pride&Prejudice): 반드시 만든 사람이 아닌, 실제로 "
                f"써본 다른 멤버. 검증 응답이 오면 게이트는 자동으로 열립니다.{rubric}{acc_v}{standard_v}{iface_v}{taste_v}{idle_note}{indep_note}{qa_note}{_horiz_note}{_thrash}")
    return None

def _gate_standard(flow, args):
    """[게이트] 최대성 마감 바인딩 — 구조적 강제(2026-06-20 사용자 "프롬프트 의존 제거"). gap_check가 standard
    (최대 표준)를 *기록*하게 강제해도, 그게 코드에 *도달*했는지는 종전엔 교차검증 standard_v 프롬프트
    (검증자 satisfice 가능)에만 맡겨 '최소 구현 통과'가 남았다. 이제 standard가 박혀 있으면 마감이 그 최대
    대비 *항목별 충족/의식적 드롭*을 result에 회계해야 한다(acceptance와 평행, 단 *외부 최대* 기준).
    flow.current.standard로 키잉(per-Task, 플래그 없음 → 매 Task 재평가). 헤더(검증/충족/회계/드롭)는 항목
    회계가 뒤따르는 통과, 탈출(N/A·불필요·면제)은 사유 필수. 교차검증 통과 *뒤* 단계(standard_v 테스트 보존).
    에러문자열 or None(통과)."""
    import re
    from .._util import _speech_clip
    _std_bind = (flow.current.standard or "").strip()
    # standard가 비었거나 그 자체가 '[최대화 N/A …]' 면제선언이면 미발동(과제한 방지 — 요구가 부를 때만 강제)
    if (_std_bind and not re.match(r"^\s*\[\s*최대화\s*(?:N\s*/?\s*A|면제|불필요)", _std_bind)
            and ("standard", flow.current.task_id) not in flow._gate_pass):   # [누적] 이미 통과면 재확인 안 함(오락가락 차단)
        _res2 = args.get("result") or ""
        # [구성요소별 분해 강제(2026-06-20 사용자: '잘된 구성인가는 구성요소로 판정 가능')] 바 헤더·한 줄
        # 회계는 satisfice라 불충분 — 헤더 뒤에 *여러 구성요소* 항목(분해)이 실제로 있어야 통과. 구성적
        # 품질은 보지 않아도 구성요소 대조로 분석 가능(taste 아님 — 천장은 좁은 지각 잔여뿐).
        _std_acc = re.search(r"\[\s*최대성\s*(?:검증|충족|회계)\s*\]([\s\S]*)", _res2)
        _std_hdr = bool(_std_acc and len(re.findall(r"\n|/|·|•|\d[).]", _std_acc.group(1))) >= 2)
        _std_na = bool(re.search(r"\[\s*최대성\s*(?:N\s*/?\s*A|불필요|면제)\s*[:：]?\s*\]?[ \t]*\S{2,}", _res2))
        if not (_std_hdr or _std_na):
            if flow.log:
                flow.log("standard_bind_gate", task=flow.current.task_id)
            return ("마감 보류(최대성 검증 — 최대판 *구성요소별* 대조 강제, 바 헤더·재호출만으론 통과 안 됨): "
                    "이 Task엔 팀이 합의한 **최대판 구성요소(분해)**가 박혀 있습니다 —\n"
                    + _speech_clip(_std_bind, 1200) + "\n\n**'좋은가?'(홀리스틱이라 satisfice됨)가 아니라 위 "
                    "*각 구성요소*가 산출물에 실제로 있나·최대만큼 깊나를 하나씩 분석**해 result에 "
                    "**'[최대성 검증]'** 헤더로 *항목별*(요소: 충족 — 증거 / [드롭] <왜 이 작품엔 과한지>, "
                    "여러 항목)을 적어 재호출하세요 — 구성적 품질은 *보지 않아도* 구성요소로 판정 가능합니다"
                    "(taste 아님). 미달·누락 요소는 그 도메인 owner에게 개선 위임 후 재검증(이게 깊이의 "
                    "반복 — 0-redo를 깨는 지점). 표준이 통째로 부적용이면 **'[최대성 N/A: <사유>]'**(빈 "
                    "재호출 통과 안 됨 — 사유 필수).")
        flow._gate_pass.add(("standard", flow.current.task_id))   # [누적·영속] 통과 기록 → 다음 호출엔 standard 건너뜀(acceptance·data_prov와 동일)
        _ckpt(flow)
    return None

def _gate_iface_dialogue(flow, args, has_product):
    """[게이트] 협업 — 인터페이스 직접 합의 강제(2026-06-22 사용자: '전문가끼리 서로 대화하는가'). interfaces(도메인
    간 계약)를 선언했는데 owner들이 서로 직접 확인(peer↔peer Info)한 적이 없으면 = 계약을 리더만 경유
    전달(사일로·중계 병목)했거나 owner가 추측한 것(P-028 API 미스매치). ≥2개 도메인이 실작업했을 때만
    (맞물릴 대상이 있을 때) 발동 — 과발동 차단. peer 직접 대화가 생기거나 '[인터페이스 직접합의 N/A:
    사유]'일 때까지 보류(persistent-until-resolved — staffing 게이트와 동형, 1회 재호출론 통과 안 됨).
    에러문자열 or None(통과)."""
    import re
    from .communication import _jobs_of, _norm_job
    _iface_x = (getattr(flow.current, "interfaces", "") or "").strip()
    _iface_na = bool(re.search(r"\[\s*인터페이스\s*직접\s*합의\s*(?:n\s*/?\s*a|면제|단독|불필요)",
                               (args.get("result") or ""), re.IGNORECASE))
    if (has_product and _iface_x and not getattr(flow.current, "peer_info_pairs", None)
            and not _iface_na and not getattr(flow, "iface_dialogue_checked", False)
            and ("iface", flow.current.task_id) not in flow._gate_pass):   # [누적] 이미 통과면 재확인 안 함
        _iwk = [m for m in flow.current.team if m != flow.leader and flow.act_by.get(m, 0) > 0]
        _idoms = {_norm_job(j) for m in _iwk for j in _jobs_of(flow._info(m) or "")} - {""}
        if len(_idoms) >= 2:
            if flow.log:
                flow.log("iface_dialogue_gate", task=flow.current.task_id)
            return ("완료 보류(인터페이스 직접 합의 — 전문가끼리 직접 대화): 이 Task는 도메인 간 "
                    "인터페이스 계약(interfaces)을 선언했는데 **owner들이 서로 직접 확인한 기록이 "
                    "없습니다**(리더만 경유 = 사일로·중계 병목, owner는 계약을 추측 → 통합 불일치). "
                    "맞물리는 도메인 owner끼리 **request(Info)로 계약을 직접 합의**하게 하세요(데이터 "
                    "포맷·API·이벤트 타이밍을 리더 중계·추측 말고 *당사자끼리*). 정말 단방향/단독이라 "
                    "직접 합의가 불필요하면 result에 **'[인터페이스 직접합의 N/A: <사유>]'**를 적어 "
                    "재호출하세요(사유 필수).")
    if _iface_x and ("iface", flow.current.task_id) not in flow._gate_pass:   # [누적·영속] iface 통과(peer 합의/N-A) 기록 → 재호출엔 건너뜀
        flow._gate_pass.add(("iface", flow.current.task_id))
        _ckpt(flow)
    return None

def _ledger_workers(flow) -> set:
    """이 Task 장부에서 **완료된 백로그의 수행자** 집합.

    [2026-07-27, U-067 실측] 기여 관문은 `flow.act_by`(이 흐름의 Write/Edit/run 횟수)로 '회의 발언만
    하고 실작업 0'을 가린다 — 옳은 취지다. 그런데 그 카운터는 **흐름 단위**라 재개하면 0에서 시작한다.
    실판에서 팀 10명이 백로그 124건을 완료했는데도 재개된 마감 흐름에서는 7명이 '실작업 0'으로 잡혀
    마감이 막혔다. 완료된 백로그는 그 사람이 실제로 만들어 인도했다는 지속 기록이므로(장부는 인도
    시점에 쓰인다), 흐름 카운터가 비어 있어도 기여로 센다 — 관문의 취지('부른 직군은 기여한다')는
    그대로 지켜지고, 정말 아무것도 안 한 사람은 여전히 잡힌다.
    """
    out = set()
    for relay in (getattr(flow, "backlog_relays", None) or {}).values():
        for b in (getattr(relay, "backlogs", None) or []):
            if getattr(b, "status", "") == "done":
                try:
                    who = int(getattr(b, "assignee", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if who:
                    out.add(who)
    return out


def _gate_contrib(flow, args, third, has_product, _engx, _scopex):
    """[게이트] 팀 기여 의무 — RFC-009. 교차 검증(cross_checks)과 **독립**. 검증이 됐어도(검증은
    기능 위주라 폴리시 부재를 못 잡음 — RFC-009 §3), 팀에 부른 직군이 이 흐름에서 회의 발언만 하고
    실작업·검증 0(act_by==0: Write/Edit/run 한 번도 없음)이면 그 도메인(타격감·그래픽·사운드·디자인·
    UX 등 폴리시)은 작품에 '반영되지 않은' 것이다 — 라이브 P-010: VFX·디자이너·모션·게임비주얼이
    실구현 0인 채 마감돼 "단순 나열 웹·타격감 없는 게임"이 됨(발언≠기여). 직군 키워드 없이 '실작업
    0'만 본다(보편 이치: 부른 직군은 기여한다, 회의 참석≠기여). [증거/명시 통과(2026-06-15 라이브
    교정): soft '1회 보류 후 재호출 통과'는 마감 관성에 무력(라이브 3/3 반사적 재호출로 폴리시 또 빠짐
    — task_contrib_overridden가 그 증거). percept와 같은 원리로 강화 — 잠수 직군이 실제로 기여하거나
    (idle 해소), 정말 불필요함을 result '[기여 불필요]'로 의식적으로 명시해야 통과. 무한 반려 아님(명시
    탈출구 상시 — 판단은 리더). 동면 복구로 act_by가 0에서 재시작해도 명시/기여로 통과(반사적 재호출은 X).]
    에러문자열 or None(통과)."""
    import re
    from .._util import _speech_clip
    if has_product and not flow.current.contrib_checked:
        _ledger = _ledger_workers(flow)   # 흐름 카운터가 비어도 장부의 완료 백로그는 실작업이다
        contrib_idle = [m for m in third
                        if flow.act_by.get(m, 0) == 0 and int(m) not in _ledger]
        _cd = bool(re.search(r"\[\s*기여\s*(?:불필요|제외|면제)\s*\]", args.get("result") or ""))
        # [흡수 차단 — [기여 불필요] 블랭킷 우회 봉쇄(2026-06-21, 라이브 P-026 규명)] 회의에 참여
        # (participated)했는데 이 Task에서 Work를 한 번도 못 받고(work_delegated_to 밖) 실작업 0인 멤버 =
        # 그 전문 도메인이 '흡수'된 것이다(리더가 전문가에게 위임 안 하고 제너럴리스트가 그 도메인까지 다 써버림
        # = 리더 독점의 핵심). 이런 멤버는 [기여 불필요] 한 줄로 묵살 못 한다 — 실제로 한 번은 위임(①)하거나
        # 팀에서 빼야(②) 한다. 위임받으면(work_delegated_to 진입) 그 뒤엔 [기여 불필요]로 마감 가능(기회는 줬다).
        # 도달 가능자만(예비·타 흐름 점유 제외) → 맡길 사람이 없으면 통과(교착 없음).
        def _reach_for_work(m):
            if str((flow._info(m) or "")).startswith("예비"):
                return False
            return not (_engx is not None and _scopex is not None and _engx.busy_elsewhere(m, _scopex))
        _part = getattr(flow.current, "participated", None) or set()
        _deleg = getattr(flow.current, "work_delegated_to", None) or set()
        _absorbed = [m for m in contrib_idle if m in _part and m not in _deleg and _reach_for_work(m)]
        if contrib_idle and (not _cd or _absorbed):
            if flow.log:
                flow.log("task_contrib_idle", task=flow.current.task_id,
                         idle=[int(m) for m in contrib_idle])
            # [RFC-009 2단계 정수 — 발언→책임] 회의록(meet 미니츠는 '[NR] 직군: 발언'으로 화자
            # 귀속)에서 잠수 직군 '본인의 발언'을 끌어와 게이트에 그대로 되돌린다 — "당신이 회의에서
            # 한 이 말이 산출물에 들어갔나?"(발언≠구현). 직군 키워드 없이 본인 발언만 에코(보편
            # 이치). 발언은 collab_notes로 Work 위임에 자동 동봉되므로(577·1562) ①로 맡기면 본인
            # 약속이 구현자=본인에게 전달돼 루프가 닫힌다 — 별도 '발언→Task' 게이트가 불필요(중복).
            notes_lines = (getattr(flow.current, "collab_notes", "") or "").splitlines()
            commits = []
            for m in contrib_idle:
                role = (flow._info(m) or "").strip()
                said = [ln.split(":", 1)[1].strip() for ln in notes_lines
                        if role and f"] {role}:" in ln]
                said = [s for s in said if s]
                if said:
                    commits.append(f"· {role}: “{_speech_clip(' / '.join(said), 240)}”")
            commit_note = ("\n[회의 발언 대조 — 발언≠구현] 아래는 이 직군들이 회의에서 한 말입니다 — "
                           "각 발언이 실제 산출물에 반영됐는지 직접 확인하고, 안 됐으면 ①로 맡기세요:\n"
                           + "\n".join(commits)) if commits else ""
            if _absorbed and flow.log:
                flow.log("task_absorbed_blocked", task=flow.current.task_id,
                         absorbed=[int(m) for m in _absorbed])
            absorbed_note = (
                f"\n\n⚠ [흡수 차단 — {flow._names(_absorbed)}은(는) '[기여 불필요]'로 넘길 수 없습니다] "
                f"이들은 **회의에서 의견을 냈는데 이 Task에서 Work 위임을 한 번도 못 받고** 실작업 0입니다 — "
                f"그 전문 도메인이 다른 사람에게 **흡수**된 것입니다(리더 독점의 핵심: 전문가에게 안 맡기고 "
                f"제너럴리스트가 그 도메인까지 다 써버림). 반드시 이들에게 request(Work)로 **실제로 맡기세요**(①) — "
                f"그래야 흡수가 풀립니다. 정말 불필요했으면 팀에서 빼세요(②). 한 번 위임한 뒤에도 본인이 안 하면 "
                f"그땐 [기여 불필요]로 마감 가능합니다(기회는 줬으니)." if _absorbed else "")
            return (
                f"완료 보류(팀 기여 의무 — 증거/명시 필요, 반사적 재호출로는 통과 안 됨): 팀의 "
                f"{flow._names(contrib_idle)}이(가) 이 흐름에서 **회의 발언 외 실작업·검증이 0**입니다"
                f"(Write/Edit/run 0회) — 이 직군의 도메인('되는가'를 넘는 그 직군의 품질·폴리시)이 "
                f"**작품에 반영되지 않았습니다**. 셋 중 하나를 택하세요: ① 필요한 도메인이면 request(Work)로 "
                f"맡겨 **실제로 만들게** 하고 그 산출물을 교차 검증까지 받으세요 ② 애초에 불필요했으면 "
                f"팀에서 빼세요(왜 불렀나=다음 학습) ③ 정말 불필요하면 result **첫 줄/본문에 "
                f"'[기여 불필요] <이유>'**를 적어 재호출하세요(의식적 판단 — 그냥 재호출로는 통과 안 됨; "
                f"'이 직군들을 뺀 채 마감'이 Task 기록에 남습니다). 특히 회의에서 '중요하다'고 한 "
                f"부분이 실제 산출물에 들어갔는지 확인하세요 — 발언만으로는 작품이 바뀌지 않습니다."
                f"{commit_note}{absorbed_note}")
        flow.current.contrib_checked = True   # 기여(idle 해소)·명시 확보 → 이 Task에선 다시 묻지 않음
    return None

def _merge_gate_args(args):
    """[B-16 — 게이트 마커 인자화(BOT_ARCH_REDESIGN 2026-07-03, 이중 수용: 인자 > regex·regex 폴백 존치)]
    회계/면제 인자를 종전 result 마커 텍스트로 합성해 result 앞에 붙인다 — 이후 모든 게이트가
    args.get('result')의 regex로 종전 그대로 판정하고, 마감 기록(status.result)에도 종전처럼 남는다
    (기계적으로 동등 — 게이트 로직 무변경). 인자 없으면 종전 동작 그대로(원본 args 반환)."""
    _b16 = []
    for _k, _fmt in (("percept_na", "[지각차원 없음: {v}]"),
                     ("visual_evidence", "[시각 검증: {v}]"),
                     ("data_source", "[데이터 출처: {v}]"),
                     ("acceptance_check", "[수용기준 검증]\n{v}"),
                     ("standard_check", "[최대성 검증]\n{v}"),
                     ("contrib_waiver", "[기여 불필요] {v}")):
        _v = str(args.get(_k) or "").strip()
        if not _v:
            continue
        # [사실대로 기록한다(2026-07-27, 전수감사)] 구조 폴백이 '[시각 미검증: 사유]'를 넘기면
        # 여기서 다시 '[시각 검증: …]'로 감싸 `[시각 검증: [시각 미검증: …]]`가 장부에 남았다 —
        # 관문은 통과하지만 기록이 사실과 반대로 읽힌다. 이미 마커 형태면 그대로 싣는다.
        if _v.startswith("[") and _v.endswith("]"):
            _b16.append(_v)
        else:
            _b16.append(_fmt.format(v=_v))
    if _b16:
        args = dict(args)
        args["result"] = ("\n".join(_b16) + "\n\n" + str(args.get("result") or "")).strip()
    return args

async def _finalize_done(flow, g, args, third, has_product):
    """[마감 확정 — 게이트 전부 통과 후] 완료 기록 합성·영속·이모지. 반환은 평문(호출자가 _ok 래핑).
    [저작 다양성 게이트 제거 — 2026-06-21 아키텍처 감사] 기존 '1~2직군이 ≥80% 저작(≥6파일) = 모놀리스'는
    순수 숫자추측(80%/6/2)이라 ① 빗나갔고(P-027: 3직군 57%인데 전문가 2명이 0저작인데도 미발동) ② '부른
    전문가가 실제로 일했나'는 기여 게이트가 act_by==0(관계적)로 더 정확히 잡아 *중복*이었다. 사용자
    지적("의미없는 숫자기반 추측 제거")에 따라 숫자 휴리스틱 게이트를 폐기 — 관계적 기여 게이트로 일원화한다.
    ('전문가를 애초에 안 부른' 과소채용은 완료시 저작%가 아니라 채용·기획 단계의 문제이고 프롬프트가 이미 유도.)"""
    import re
    from .._util import _react, _speech_clip
    done_ref = flow.current
    # 허위보고 차단(도메인 무관): 완료의 '진짜'는 에이전트 산문이 아니라 시스템이 캡처한 실행 영수증.
    # 코드는 합격/불합격을 판단하지 않고(하드코딩·QA역할 가정 X), 보고 옆에 실제 출력을 떼어낼 수 없게 묶는다.
    report = _speech_clip(args.get("result") or "", 800)   # Task 블록(Discord 2000 한도) 안에 들어가는 요약
    # [침묵 강행 불가] 검증 분업 보류를 재호출로 강행한 '단독 마감'은 기록에 그렇게 보이게 한다
    # ("자를 수는 있어도 조용히는 못 자른다"의 마감 버전) — 행동은 막지 않되(자동 회사·리더 판단),
    # 사후 분석·사용자가 한눈에 보게(범용 이치의 구조 잠금, 사용자 승인 2026-06-12).
    solo = bool((flow.current.owner or getattr(flow.current, "leader_writes", 0) > 0)
                and flow.current.cross_checks == 0)
    if solo and flow.log:
        flow.log("task_solo_completed", task=flow.current.task_id, owner=int(flow.current.owner or 0))
    # [기여 미흡 마감 가시화 — RFC-009, 침묵 강행 불가] 게이트 1회 보류를 재호출로 통과해(옵션③)
    # 잠수 직군이 여전히 실작업 0인 채 마감되면, '이 직군들을 뺀 채 마감'을 결과에 박아 영속한다 —
    # 라이브 3/3 게이트가 전부 반사적 재호출로 통과해 폴리시가 또 빠짐(사용자 지적). 행동은 막지
    # 않되(리더 자율) 사후 분석·사용자·학습이 한눈에 보게(단독 마감 마커와 같은 정신). 직군 키워드 없음.
    contrib_idle_now = [m for m in third if flow.act_by.get(m, 0) == 0] if has_product else []
    if contrib_idle_now and flow.log:
        flow.log("task_contrib_overridden", task=flow.current.task_id,
                 idle=[int(m) for m in contrib_idle_now])
    done_ref.status.status = "완료"
    # [완주 관측 구멍 메움(2026-07-26, 전 기간 로그 감사)] 종전엔 마감 성공 경로가 **아무 이벤트도
    # 내지 않았다** — 조건부 신호(단독 마감·기여 미흡·역량 적립)가 우연히 걸릴 때만 흔적이 남아,
    # 로그만으로는 '완주한 판'과 '중단된 판'을 구분할 수 없었다(전 기간 완주 3회를 부산물로만 확인).
    # 마감은 파이프라인의 종착이므로 무조건 관측 가능해야 한다.
    if flow.log:
        try:
            flow.log("task_completed", task=str(getattr(done_ref, "task_id", "") or ""),
                     owner=int(getattr(done_ref.status, "owner", 0) or 0),
                     cross_checks=int(getattr(done_ref, "cross_checks", 0) or 0),
                     offdomain=int(getattr(done_ref, "cross_check_offdomain", 0) or 0))
        except Exception:
            pass
    # [보고=관찰, 주장 아님 — '거짓말'의 *핵심* 교정(2026-06-20 라이브 P-025)] 봇 narrative의 URL은
    # confabulate된다(봇이 *요청한* 이름 taas-…를 URL로 보고 → 404; 실제 배포는 시스템이 캐논 슬롯
    # organt-p-NNN으로 _check_live 검증해 flow.deployed에 보유). 게이트를 차원마다 N개 더 다는 대신,
    # 시스템이 *관찰한 사실*(검증된 배포 URL)을 *권위*로 주입하고 봇이 보고한 다른 onrender URL은 '무효'로
    # 박는다 — 이미 있는 [시스템 실행기록](run 영수증) 주입과 같은 원리를 배포 URL로 확장(보고를 관찰에 묶음).
    _url_truth = ""
    _auth = re.search(r"https://[a-z0-9-]+\.onrender\.com", str(getattr(flow, "deployed", "") or ""))
    if _auth:
        _au = _auth.group(0)
        _wrong = {u for u in re.findall(r"https://[a-z0-9-]+\.onrender\.com", report) if u != _au}
        _url_truth = (f"[시스템 검증 — 라이브 URL(권위)] {_au}"
                      + (f"  ⚠ 봇 보고 {', '.join(sorted(_wrong))} 는 배포된 적 없는 무효 링크"
                         if _wrong else "") + "\n")
    done_ref.status.result = (
        _url_truth
        + (f"[검증: 단독 마감 — 교차 검증 0, 리더 판정만]\n" if solo else "")
        + (f"[기여 미흡: {flow._names(contrib_idle_now)} 실작업 0 — 리더 판단으로 마감(폴리시 미반영 가능)]\n"
           if contrib_idle_now else "")
        + f"[보고] {report}\n"
        f"[시스템 실행기록 {done_ref.run_count}회·마지막] {done_ref.evidence or '(없음)'}"
    )[:1400]
    await flow.refresh(done_ref)
    await _react(g, flow.project_channel, done_ref.block_id, "✅")  # 완료=이모지
    # [완료 보고 = 시스템 종합(2026-07-14, 사용자: '한 명이 올리는 게 아니라 결정된 보고안을 Task 하위에')]
    # 개인 result 대신, 판 전체의 *검증된 기록*(목표·마일스톤별 완수조건·배포·참여)에서 시스템이 종합한
    # 완료 보고를 시스템 명의(sender 0)로 게시한다 — 내용은 특정 한 명의 서사가 아니라 팀이 실제로 통과시킨
    # 것들의 집합. 피드는 이 '[완료 보고]'를 Task 하위 끝 섹션으로 형식화해 보여준다(개인 result는 근거로 동봉).
    try:
        _rl = [f"[완료 보고] {done_ref.task_id} 완료"]
        _g = str(getattr(done_ref.status, "goal", "") or "").strip()
        if _g:
            _rl.append(f"· 목표: {_g[:180]}")
        for _ms in (getattr(flow, "milestones", None) or []):
            if _ms.status != "done":
                continue
            _met = sum(1 for _c in _ms.criteria if _c.passed)
            _tot = len(_ms.criteria)
            _rl.append(f"· ✓ {_ms.goal[:70]} — 완수조건 {_met}/{_tot}")
        if _auth:
            _rl.append(f"· 배포: {_au}")
        _tm = ", ".join(flow._names(list(getattr(done_ref, "team", None) or []))[:12])
        if _tm:
            _rl.append(f"· 참여: {_tm}")
        _sumline = re.sub(r"\s+", " ", (report or "")).strip()
        if _sumline:
            _rl.append(f"· 요약: {_sumline[:400]}")
        await g.post(int(flow.project_channel), 0, "\n".join(_rl)[:1600])
    except Exception:
        pass
    _ledger_accrue(flow)              # [B-21] 품질 게이트 통과분만 실적 영속(흡수형 저작은 여기서 폐기)
    flow.current = None
    _ckpt(flow)                       # 크래시-세이프: 마감 즉시 '미완 없음'으로 영속(유령 복원 방지)
    return f"task={done_ref.task_id} 완료 마감 (시스템 실행기록 {done_ref.run_count}회 첨부)"
