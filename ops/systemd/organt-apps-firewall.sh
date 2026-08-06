#!/bin/sh
# [앱 풀은 로컬 전용이다(2026-08-06 감사, 현준-4)] system/deploy.py는 4100-4199를 "로컬 전용 —
# 게이트웨이만 접근"이라 적어 두지만, 앱은 봇이 쓴 코드라 대개 0.0.0.0에 바인딩한다. 그러면
# /apps/<슬롯>/에 건 멤버 확인을 포트로 직접 가면 지나친다(외부는 공유기가 막지만 같은 랜은 아니다).
# 뜻을 강제한다: 이 대역은 루프백에서만 받는다. 미디어(SFU UDP·TCP 7881)는 건드리지 않는다.
set -e
iptables -D INPUT -p tcp --dport 4100:4199 ! -i lo -j DROP 2>/dev/null || true
iptables -A INPUT -p tcp --dport 4100:4199 ! -i lo -j DROP
