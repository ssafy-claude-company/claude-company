"""[표결 서사 백필(2026-07-21, 사용자: '구 데이터도 복구해내봐 저 디자인대로')] 개별 사유 수집 이전에
게시된 [표] 집계 메시지에, flow.jsonl의 consensus_ratify_vote(who·vote)를 매칭해 투표자별 표를
복원하고 '투표: 이름 | 표 [| 사유]' 정본 줄을 append한다(feed_assembly가 카드로 렌더).

복원 범위(정직): who+vote는 flow 로그에서 완전 복원 · **반대 사유는 집계의 '반대 요지:'에서 순서
매칭(부결 표결만)** · 찬성/기권 사유는 로그에 없어 복원 불가(표만). 멱등('투표:' 이미 있으면 스킵).

실행: cd murmur/backend && DATABASE_URL=... PYTHONPATH=/root/ClaudeCompany python manage.py shell \
      -c "exec(open('/root/ClaudeCompany/ops/tools/backfill_vote_ballots.py').read())"   (dry_run 기본)
적용: 같은 명령 앞에 BACKFILL_APPLY=1 env.
"""
import json
import os
import re

FLOW = "/root/ClaudeCompany/ops/var/organt_sns_state/flow.jsonl"
APPLY = os.environ.get("BACKFILL_APPLY") == "1"
_VLABEL = {"for": "찬성", "against": "반대", "abstain": "기권"}


def _ratify_clusters():
    evs = []
    for ln in open(FLOW):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if e.get("event") == "consensus_ratify_vote":
            evs.append(e)
    by = {}
    for e in evs:
        by.setdefault(e.get("pid"), []).append(e)
    out = {}
    for pid, xs in by.items():
        xs.sort(key=lambda e: e["ts"])
        rounds, cur = [], []
        for e in xs:
            if cur and e["ts"] - cur[-1]["ts"] > 10:
                rounds.append(cur)
                cur = []
            cur.append(e)
        if cur:
            rounds.append(cur)
        out[pid] = rounds
    return out


def run():
    from sns.models import GuideMessage, Agent
    names = {a.bot_id: a.name or a.role for a in Agent.objects.all()}
    clusters = _ratify_clusters()
    posts = list(GuideMessage.objects.filter(sender_id=0, body__startswith="[표]").order_by("msg_id"))
    done = skip = 0
    for gm in posts:
        # 이미 투표 줄이 있고 @id까지 실렸으면 완성 — 스킵. @없는 구 백필분은 재처리(id 보강).
        if "투표:" in (gm.body or "") and "@" in (gm.body or ""):
            skip += 1
            continue
        if "투표:" in (gm.body or ""):
            gm.body = re.sub(r"\n투표:.*$", "", gm.body or "", flags=re.S)   # 구 백필 투표줄 제거 후 재생성
        pid = gm.channel_id
        rounds = clusters.get(pid) or []
        # 이 게시 직전(ts ≤ post, 120s 내)에 끝난 라운드 = 이 표결
        cand = [r for r in rounds if r and r[-1]["ts"] <= gm.ts + 2 and gm.ts - r[-1]["ts"] < 300]
        if not cand:
            continue
        rd = max(cand, key=lambda r: r[-1]["ts"])
        # 집계에서 찬반 수 파싱(개별 vote가 None인 구 로그의 복원 규칙 판정)
        _am = re.search(r"반대\s*(\d+)", gm.body or "")
        n_against = int(_am.group(1)) if _am else 0
        _have_vote = all(e.get("vote") in ("for", "against", "abstain") for e in rd)
        if not _have_vote:
            if n_against == 0:
                for e in rd:            # 반대 0 → 전원 찬성(확정 복원). who는 로그에 있음.
                    e["vote"] = "for"
            else:
                continue                # 개별 표 미상+반대 존재 → 오배정 금지, 집계 카드만(요약 폴백)
        # 반대 요지(부결 표결) 순서 매칭 → against 투표자에 배정
        _dm = re.search(r"반대 요지:\s*(.+)$", gm.body or "")
        dreasons = [x.strip() for x in _dm.group(1).split(" · ")] if _dm else []
        di = 0
        lines = []
        for e in rd:
            who = names.get(e.get("who"), str(e.get("who")))
            _wid = e.get("who")
            vt = _VLABEL.get(e.get("vote"), e.get("vote"))
            rs = ""
            if e.get("vote") == "against" and di < len(dreasons):
                rs = dreasons[di]
                di += 1
            # 이름@봇id(공용 아바타 프로필용) — 백필도 flow 로그의 who(봇id) 동봉
            _wtag = f"{who}@{int(_wid)}" if isinstance(_wid, int) else who
            lines.append(f"투표: {_wtag} | {vt}" + (f" | {rs}" if rs else ""))
        # 요약 줄(반대 요지 이하 제거 — 카드가 사유를 담음)
        head = re.sub(r"\s*/\s*반대 요지:.*$", "", gm.body or "").rstrip()
        new_body = head + "\n" + "\n".join(lines)
        done += 1
        if done <= 3:
            print("--- ch%s msg%s\n%s" % (pid, gm.msg_id, new_body[:300]))
        if APPLY:
            gm.body = new_body
            gm.save(update_fields=["body"])
    print("\n복원 대상 %d건 · 이미 상세 %d건 · %s" % (done, skip, "적용됨" if APPLY else "DRY-RUN(미적용)"))


run()
