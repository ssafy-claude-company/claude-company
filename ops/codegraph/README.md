# 코드 지도 (dev 도구)

코드를 파일로 읽지 않고 구조에 피드백하기 위한 **개발 영역 도구**다. murmur 제품이 아니다
(2026-07-11 사용자 판정 — murmur PR #11 닫고 이곳으로 이식).

- **무엇**: ClaudeCompany 전체(system·organt·guide·ops·murmur)를 파일=노드, import=엣지로
  그린 그래프 뷰어. 노드 클릭 = 파일 설명(docstring)·쓰는 곳/쓰이는 곳.
- **피드백**: 📌 모드에서 노드 클릭 → 핀+코멘트. murmur 피드백 서비스의 이식 계약을 쓴다
  (`service=codegraph`, 같은 오리진의 `/api/feedback/`, admin 토큰 = localStorage `organt_token`).
  처리 루프는 기존 그대로: `manage.py feedback_backlog` → 세션이 고침 → `feedback_resolve`.

## 사용

```bash
python3 ops/codegraph/scan.py     # 그래프 데이터 재생성 → codegraph.json (코드 바뀌면 재실행)
```

서빙: 이 폴더를 정적으로 서빙하면 끝(단일 html+json). 핀을 쓰려면 murmur와 **같은 오리진**
아래 경로여야 한다(예: nginx `location /dev/codegraph/ { alias .../ops/codegraph/; }`).
다른 오리진이면 핀 API가 CORS·토큰에서 막힌다 — 열람만 가능.

## 파일

- `scan.py` — 스캐너(ast import 해석 + 프론트 상대 import). 봇 산출물(ops/var)·테스트
  스위트·migrations 제외(meta.excluded에 명시).
- `index.html` — 뷰어+핀 레이어(의존 0, 단일 파일).
- `codegraph.json` — 생성물 스냅샷(재생성 가능).
