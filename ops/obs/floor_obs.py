#!/usr/bin/env python3
"""1층 floor(turn-taking) 관측 잔여 실측 — BACKLOG G 마지막 항목 (2026-07-18).

flow.jsonl(라이브 러너 관측 로그)만으로 오프라인 계산한다 — 봇 기동 0.
선택: --dburl 주면 암시 지명(발언 본문에 이름 언급 후 그 봇이 응찰 승) 빈도를
sns_guidemessage 본문 조인으로 추정한다(읽기 전용 SELECT만).

측정 항목(BACKLOG G '1층 관측 잔여'):
  A. 응찰 분포 시계열 — 인플레/보정(H2 지표)
  B. 응찰 wake 비용 실측 + wake 축소 knob(큐·쿨다운) 근거 데이터
  C. 종결 표결 — 조기종결 순효과·[계속] 소생 갈래 라이브 발생
  D. 지명 — 명시(nominate) 빈도·암시(soft nomination) 추정
  E. 기여 귀속 — 수렴안 제출자 vs 발언권 분포(proxy)

사용: .venv/bin/python ops/obs/floor_obs.py [--flow PATH] [--dburl URL|@FILE] [--json PATH]
"""
import argparse
import collections
import datetime
import json
import statistics
import subprocess
import sys

FLOW_DEFAULT = "/root/ClaudeCompany/ops/var/organt_sns_state/flow.jsonl"


def day(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d")


def bucket3(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return (d - datetime.timedelta(days=(d.timetuple().tm_yday - 1) % 3)).strftime("%m-%d")


def pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def load(path):
    evs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evs.append(json.loads(line))
            except Exception:
                pass
    return evs


def score_stats(scores):
    if not scores:
        return "n=0"
    n = len(scores)
    hi = sum(1 for s in scores if s >= 8)
    one = sum(1 for s in scores if s <= 1)
    return (f"n={n:<5d} 평균 {statistics.mean(scores):.2f}  p50 {statistics.median(scores):.0f}"
            f"  ≥8 {pct(hi, n):>6}  ≤1 {pct(one, n):>6}")


def sec_a_bids(evs, out):
    """A. 응찰 분포 시계열 — 인플레(점수 상향 표류)/관성(1점 도배) 관측."""
    print("\n== A. 응찰 분포 시계열 (H2 인플레/보정) ==")
    by_key = collections.defaultdict(list)
    for e in evs:
        if e.get("event") == "floor_bid":
            by_key[(e.get("surface"), bucket3(e["ts"]))].append(e.get("score", 0))
        elif e.get("event") == "propose_bid":
            by_key[("propose", bucket3(e["ts"]))].append(e.get("score", 0))
    for surface in ("meet", "segment", "propose"):
        print(f"[{surface}]")
        rows = sorted(k for k in by_key if k[0] == surface)
        for k in rows:
            print(f"  {k[1]}  {score_stats(by_key[k])}")
        allscores = [s for k in rows for s in by_key[k]]
        print(f"  전체    {score_stats(allscores)}")
        out[f"bids_{surface}"] = {k[1]: len(by_key[k]) for k in rows}
    # 발제 선거 pass율·bidders
    p_bid = sum(1 for e in evs if e.get("event") == "propose_bid")
    p_pass = sum(1 for e in evs if e.get("event") == "propose_pass")
    elected = [e for e in evs if e.get("event") == "propose_elected"]
    bidders = [e.get("bidders", 0) for e in elected if e.get("bidders")]
    print(f"[발제 선거] 응찰 {p_bid} · 패스 {p_pass} (패스율 {pct(p_pass, p_bid + p_pass)}) · "
          f"선출 {len(elected)}건, 선거당 bidders 평균 {statistics.mean(bidders):.1f} "
          f"최대 {max(bidders)}" if bidders else "[발제 선거] 데이터 없음")
    out["propose"] = {"bids": p_bid, "pass": p_pass, "elected": len(elected),
                      "bidders_mean": statistics.mean(bidders) if bidders else 0}


def build_traces(evs):
    """trace_id 있는 이벤트를 판 단위로 묶는다(07-07 02:5x 이후 = 커버 98%+)."""
    traces = collections.defaultdict(list)
    for e in evs:
        t = e.get("trace_id")
        if t:
            traces[t].append(e)
    for t in traces:
        traces[t].sort(key=lambda e: (e.get("ts", 0), e.get("seq", 0)))
    return traces


def match_probe_costs(traces):
    """floor_bid ↔ turn_done 조인(같은 trace·bot==who·ts −600s~+5s, 소비 1회) → 프로브 실비.

    일별 프로브$는 매칭된 turn_done 실비 합산(외삽 아님 — 매칭률 99%+라 오차 = 미매칭분뿐).
    창 = [직전 alloc(수집 개시), 응찰 배치 기록]로 조여 발언 turn의 프로브 오귀속을 차단.
    다턴 매칭(프로브 중 도구 사용 vs 오귀속)은 판별 불가라 상·하한을 함께 낸다."""
    probe_costs, speech_costs = [], []
    probe_daily = collections.defaultdict(float)
    probe_1turn_costs = []
    matched = unmatched = 0
    for evs in traces.values():
        tds = [e for e in evs if e.get("event") == "turn_done"]
        used = set()
        last_alloc_ts = {}   # surface -> 직전 alloc ts(수집 창 시작)
        for e in evs:
            if e.get("event") == "floor_alloc":
                last_alloc_ts[e.get("surface")] = e["ts"]
            if e.get("event") == "floor_bid":
                w, ts = e.get("who"), e["ts"]
                lo = last_alloc_ts.get(e.get("surface"), ts - 600) - 5
                cand = [t for t in tds if id(t) not in used and t.get("bot") == w
                        and lo <= t["ts"] <= ts + 5]
                if cand:
                    td = max(cand, key=lambda t: t["ts"])
                    used.add(id(td))
                    c = td.get("cost_usd") or 0.0
                    probe_costs.append(c)
                    probe_daily[day(td["ts"])] += c
                    if td.get("num_turns") == 1:
                        probe_1turn_costs.append(c)
                    matched += 1
                else:
                    unmatched += 1
            elif e.get("event") == "floor_alloc" and e.get("kind") in ("self", "nominate"):
                nxt, ts = e.get("nxt"), e["ts"]
                cand = [t for t in tds if id(t) not in used and t.get("bot") == nxt
                        and ts <= t["ts"] <= ts + 900]
                if cand:
                    td = min(cand, key=lambda t: t["ts"])
                    used.add(id(td))
                    speech_costs.append(td.get("cost_usd", 0.0))
    return probe_costs, speech_costs, matched, unmatched, probe_daily, probe_1turn_costs


def sec_b_cost(evs, traces, out):
    """B. 응찰 wake 비용 + knob 근거 — 프로브 실비·일별 프로브 수·open당 재프로브 인원."""
    print("\n== B. 응찰 wake 비용 실측 (knob 근거) ==")
    probe_costs, speech_costs, matched, unmatched, probe_daily, probe_1t = match_probe_costs(traces)
    p_mean = statistics.mean(probe_costs) if probe_costs else 0.0
    s_mean = statistics.mean(speech_costs) if speech_costs else 0.0
    print(f"프로브($) 조인: 매칭 {matched} / 미매칭 {unmatched} (매칭률 {pct(matched, matched + unmatched)})")
    if probe_costs:
        print(f"  응찰 프로브 1회 = 평균 ${p_mean:.4f} · p50 ${statistics.median(probe_costs):.4f}"
              f" · p90 ${sorted(probe_costs)[int(len(probe_costs) * 0.9)]:.4f}")
    if speech_costs:
        print(f"  발언 1회      = 평균 ${s_mean:.4f} · p50 ${statistics.median(speech_costs):.4f} (n={len(speech_costs)})")
    # 일별 총비용 vs 프로브 실비(매칭 합산 — 외삽 아님)
    daily_cost = collections.defaultdict(float)
    daily_bids = collections.Counter()
    for e in evs:
        if e.get("event") == "turn_done":
            daily_cost[day(e["ts"])] += e.get("cost_usd") or 0.0
        elif e.get("event") == "floor_bid":
            daily_bids[day(e["ts"])] += 1
    print("일자   총$      floor_bid수  프로브$실측   몫")
    for d in sorted(daily_cost):
        pb = probe_daily.get(d, 0.0)
        print(f"{d}  {daily_cost[d]:8.2f}  {daily_bids.get(d, 0):6d}     {pb:8.2f}  {pct(pb, daily_cost[d]):>6}")
    tot = sum(daily_cost.values())
    tot_probe = sum(probe_daily.values())
    tot_1t = sum(probe_1t)
    print(f"합계   {tot:8.2f}  {sum(daily_bids.values()):6d}     {tot_probe:8.2f}"
          f"  (프로브 몫 = {pct(tot_probe, tot)}, 매칭 실비 기준)")
    print(f"프로브 몫 하한(1턴 매칭만 프로브로 집계): ${tot_1t:.2f} = {pct(tot_1t, tot)} · "
          f"1턴 비율 {pct(len(probe_1t), len(probe_costs))} (다턴 매칭 = 도구 쓴 프로브 또는 오귀속, 판별 불가)")
    # open당 응찰 인원(재프로브 크기) — meet
    per_open = []
    for evs_t in traces.values():
        cur = None
        for e in evs_t:
            if e.get("event") == "floor_alloc" and e.get("surface") == "meet":
                if e.get("kind") == "open":
                    cur = 0
                else:
                    if cur is not None and cur > 0:
                        per_open.append(cur)
                    cur = None
            elif e.get("event") == "floor_bid" and e.get("surface") == "meet" and cur is not None:
                cur += 1
    if per_open:
        print(f"open당 응찰 인원(meet): 평균 {statistics.mean(per_open):.1f} · p50 "
              f"{statistics.median(per_open):.0f} · 최대 {max(per_open)} (n={len(per_open)})")
    out["cost"] = {"probe_usd_mean": p_mean, "speech_usd_mean": s_mean,
                   "total_usd": tot, "probe_share": tot_probe / tot if tot else 0,
                   "probe_share_floor": tot_1t / tot if tot else 0,
                   "bids_per_open_mean": statistics.mean(per_open) if per_open else 0}


def sec_c_closure(traces, out):
    """C. 종결 표결 — 결과 내역·[계속] 소생 발언 이행·표결 실측 비용."""
    print("\n== C. 종결 표결 (조기종결·[계속] 소생) ==")
    n_votes = n_close = n_revive = 0
    revive_spoke = 0
    vote_costs = []
    for evs_t in traces.values():
        allocs = [e for e in evs_t if e.get("event") == "floor_alloc" and e.get("surface") == "meet"]
        tds = [e for e in evs_t if e.get("event") == "turn_done"]
        for i, a in enumerate(allocs):
            if a.get("kind") != "close_vote":
                continue
            n_votes += 1
            nxt_alloc = allocs[i + 1] if i + 1 < len(allocs) else None
            end_ts = nxt_alloc["ts"] if nxt_alloc else a["ts"] + 600
            wakes = [t for t in tds if a["ts"] < t["ts"] <= end_ts + 5]
            vote_costs.append(sum(t.get("cost_usd") or 0.0 for t in wakes))
            if nxt_alloc is None:
                continue
            if nxt_alloc.get("kind") == "close":
                n_close += 1
            elif str(nxt_alloc.get("reason", "")).startswith("종결 반대"):
                n_revive += 1
                who = nxt_alloc.get("nxt")
                if any(t.get("bot") == who and t["ts"] > nxt_alloc["ts"] for t in tds):
                    revive_spoke += 1
    print(f"표결 {n_votes}건 → 합의 종결 {n_close} · [계속] 소생 {n_revive} (소생자 실제 발언 {revive_spoke})")
    if vote_costs:
        print(f"표결 1회 실측 비용: 평균 ${statistics.mean(vote_costs):.4f} · 총 ${sum(vote_costs):.2f}")
    out["closure"] = {"votes": n_votes, "close": n_close, "revive": n_revive,
                      "revive_spoke": revive_spoke,
                      "vote_usd_total": sum(vote_costs)}


def sec_d_nominate(evs, traces, dburl, out):
    """D. 지명 — 명시 nominate 빈도 + (DB 있으면) 암시 지명 추정."""
    print("\n== D. 지명 (명시·암시) ==")
    meet_allocs = [e for e in evs if e.get("event") == "floor_alloc" and e.get("surface") == "meet"
                   and e.get("kind") in ("self", "nominate")]
    n_nom = sum(1 for e in meet_allocs if e.get("kind") == "nominate")
    print(f"발언권 배분(meet) {len(meet_allocs)}건 중 명시 지명 {n_nom} ({pct(n_nom, len(meet_allocs))}) · "
          f"자기선택 {len(meet_allocs) - n_nom}")
    out["nominate"] = {"allocs": len(meet_allocs), "explicit": n_nom}
    if not dburl:
        print("암시 지명: --dburl 없음 → 생략")
        return
    # trace→channel 지도: propose_*(channel 보유) + pid 보유 이벤트 → pid↔channel 전역 누적
    tr_ch, tr_pid, pid_ch = {}, {}, {}
    for t, evs_t in traces.items():
        for e in evs_t:
            if "channel" in e:
                tr_ch[t] = e["channel"]
            if "pid" in e:
                tr_pid[t] = e["pid"]
    for t, ch in tr_ch.items():
        if t in tr_pid:
            pid_ch[tr_pid[t]] = ch
    for t, p in tr_pid.items():
        if t not in tr_ch and p in pid_ch:
            tr_ch[t] = pid_ch[p]
    # COPY csv — 본문 개행이 행을 깨지 않게(단순 -A 파싱은 다행 본문을 첫 줄에서 절단한다)
    def qcsv(sql):
        import csv, io
        r = subprocess.run(["psql", dburl, "-c", f"COPY ({sql}) TO STDOUT WITH (FORMAT csv)"],
                           capture_output=True, text=True)
        return list(csv.reader(io.StringIO(r.stdout)))
    names = {}
    for row in qcsv("SELECT bot_id, name FROM sns_agent"):
        if len(row) == 2 and row[0]:
            names[int(row[0])] = row[1].strip()
    msgs = collections.defaultdict(list)   # channel -> [(ts, sender, body)]
    for row in qcsv("SELECT channel_id, sender_id, ts, body FROM sns_guidemessage "
                    "WHERE body IS NOT NULL AND length(body) > 20"):
        if len(row) == 4 and row[0]:
            msgs[int(row[0])].append((float(row[2] or 0), int(row[1] or 0), row[3]))
    for ch in msgs:
        msgs[ch].sort()
    evaluable = soft = soft_marker = 0
    for t, evs_t in traces.items():
        ch = tr_ch.get(t)
        if ch is None or ch not in msgs:
            continue
        for e in evs_t:
            if (e.get("event") == "floor_alloc" and e.get("surface") == "meet"
                    and e.get("kind") == "self" and e.get("nxt") in names):
                wname = names[e["nxt"]]
                prior = [m for m in msgs[ch] if e["ts"] - 1800 <= m[0] < e["ts"] and m[1] != e["nxt"]]
                if not prior:
                    continue
                evaluable += 1
                if wname and wname in prior[-1][2]:
                    soft += 1
                    if "지명" in prior[-1][2]:
                        soft_marker += 1   # '지명' 문구까지 있었는데 kind=self = 지명 파싱 실패 의심
    # 기준선: 봇 발언 전체에서 '타 봇 이름'이 언급되는 비율(0의 해석용 — 이름 문화 자체가 없으면
    # 암시 지명 0은 조인 결함이 아니라 실제 부재)
    all_names = set(names.values())
    n_msgs = mention = 0
    for ch in msgs:
        for ts, snd, body in msgs[ch]:
            if snd not in names:
                continue
            n_msgs += 1
            if any(nm and nm != names.get(snd) and nm in body for nm in all_names):
                mention += 1
    print(f"암시 지명(직전 타인 발언에 승자 이름 언급, kind=self): {soft}/{evaluable} "
          f"({pct(soft, evaluable)}) — 그중 '지명' 문구 동반 {soft_marker} · trace→채널 매핑 {len(tr_ch)}개 커버")
    print(f"기준선: 봇 발언 {n_msgs}건 중 타 봇 이름 언급 {mention} ({pct(mention, n_msgs)})")
    out["nominate"]["soft"] = soft
    out["nominate"]["soft_evaluable"] = evaluable
    out["nominate"]["soft_with_marker"] = soft_marker
    out["nominate"]["name_mention_base"] = [mention, n_msgs]


def sec_e_attribution(traces, out):
    """E. 기여 귀속 proxy — 수렴안 조기 제출자가 그 판 발언권 분포에서 어디였나."""
    print("\n== E. 기여 귀속 (수렴안 제출자 vs 발언권, proxy) ==")
    n = top = zero = 0
    for evs_t in traces.values():
        subs = [e for e in evs_t if e.get("event") == "consensus_in_discussion"]
        if not subs:
            continue
        wins = collections.Counter(e.get("nxt") for e in evs_t
                                   if e.get("event") == "floor_alloc" and e.get("surface") == "meet"
                                   and e.get("kind") in ("self", "nominate"))
        for s in subs:
            w = s.get("who")
            n += 1
            if not wins:
                continue
            if wins.get(w, 0) == 0:
                zero += 1
            elif wins.get(w, 0) == max(wins.values()):
                top += 1
    print(f"수렴안 조기 제출 {n}건: 제출자=그 판 발언권 최다 {top} ({pct(top, n)}) · "
          f"발언권 0회 제출 {zero} ({pct(zero, n)})")
    print("(본문 수준 '아이디어 생존' 추적은 flow 로그에 발언 내용이 없어 측정 불가 — proxy만)")
    out["attribution"] = {"submits": n, "submitter_top": top, "submitter_zero_wins": zero}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow", default=FLOW_DEFAULT)
    ap.add_argument("--dburl", default=None, help="postgres URL 또는 @파일경로 (읽기 전용 SELECT만)")
    ap.add_argument("--json", default=None, help="수치 요약 JSON 저장 경로")
    args = ap.parse_args()
    dburl = args.dburl
    if dburl and dburl.startswith("@"):
        dburl = open(dburl[1:]).read().strip()
    evs = load(args.flow)
    ts = [e["ts"] for e in evs if "ts" in e]
    print(f"flow.jsonl: 이벤트 {len(evs)}건, {day(min(ts))} ~ {day(max(ts))}")
    traces = build_traces(evs)
    print(f"trace 판: {len(traces)}개 (trace_id 보유 이벤트만 — 07-07 이후 커버 98%+)")
    out = {}
    sec_a_bids(evs, out)
    sec_b_cost(evs, traces, out)
    sec_c_closure(traces, out)
    sec_d_nominate(evs, traces, dburl, out)
    sec_e_attribution(traces, out)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nJSON 저장: {args.json}")


if __name__ == "__main__":
    sys.exit(main())
