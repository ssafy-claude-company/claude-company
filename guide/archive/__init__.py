"""[아카이브] Discord 시대 구현체 — 라이브 경로 아님, 회귀 검증 안 됨.

여기 있는 것: discord_guide(전송기)·discord_main(리스너 엔트리)·channels(채널 유틸).
왜 삭제가 아니라 아카이브인가: Discord 매체 이행(BACKLOG E)의 코어(guide_queue·intake)는
살아 있고, 이 구현체들은 그 이행 재개 시 출발점이다.
옛 경로(guide.discord_guide 등)는 재수출 shim으로 유지 — 소비처(ops/organt_discord shim·
ops/tests)는 경로 무변경. 재활성화 조건: discord.py 토큰 + 라이브 검증(ARCHITECTURE §6).
"""
