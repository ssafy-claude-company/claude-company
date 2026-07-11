/* dev 공용 피드백 핀 레이어 — 페이지의 **모든 요소**에 핀을 단다(murmur FeedbackLayer와 동일 UX).
   셀렉터 생성·복원은 murmur frontend/src/fbdom.js의 알고리즘 이식(#id 우선, tag.class:nth 체인).
   사용: 페이지에서 <script>window.FB_SERVICE='codegraph'</script> 후 이 파일 로드 — 우하단 📌 토글.
   앵커 계약(route+selector+anchor_text+pos%)은 FEEDBACK_SERVICE.md 그대로. */
(function () {
  const SERVICE = window.FB_SERVICE || 'dev'
  const API = window.FB_API || (location.pathname.replace(/[^/]*$/, '').replace(/codegraph\/$/, '') + 'api/feedback/')
  const ROUTE = location.pathname

  /* ── 토큰·HTTP ── */
  const token = () => {
    let t = localStorage.getItem('organt_token')
    if (!t) { t = prompt('admin 토큰(organt_token)'); if (t) localStorage.setItem('organt_token', t) }
    return t || ''
  }
  const hdr = () => ({ Authorization: 'Token ' + token(), 'Content-Type': 'application/json' })
  const jget = async (u) => { const r = await fetch(u, { headers: hdr() }); if (!r.ok) throw new Error(r.status); return r.json() }
  const jpost = async (u, b) => { const r = await fetch(u, { method: 'POST', headers: hdr(), body: JSON.stringify(b) }); if (!r.ok) throw new Error(r.status); return r.json() }

  /* ── 셀렉터(fbdom 이식) ── */
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`))
  function seg(el) {
    const tag = el.tagName.toLowerCase()
    const cls = el.classList && el.classList.length ? `.${esc(el.classList[0])}` : ''
    let nth = ''
    const p = el.parentElement
    if (p) {
      const sibs = Array.prototype.filter.call(p.children, (c) => c.tagName === el.tagName)
      if (sibs.length > 1) nth = `:nth-of-type(${sibs.indexOf(el) + 1})`
    }
    return tag + cls + nth
  }
  function chain(el, depth) {
    const parts = []
    let cur = el
    while (cur && cur.nodeType === 1 && cur.tagName !== 'HTML' && cur.tagName !== 'BODY' && parts.length < depth) {
      if (cur.id) { parts.unshift(`#${esc(cur.id)}`); return parts.join(' > ') }
      parts.unshift(seg(cur))
      cur = cur.parentElement
    }
    return parts.join(' > ')
  }
  function buildSelector(el) {
    if (!el || el.nodeType !== 1) return ''
    if (el.id) { const s = `#${esc(el.id)}`; if (document.querySelector(s) === el) return s }
    let last = ''
    for (let d = 6; d <= 20; d++) {
      const s = chain(el, d)
      if (!s) break
      try { if (document.querySelector(s) === el) return s } catch (e) { /* 더 깊게 */ }
      if (s === last) break
      last = s
    }
    return last || (el.tagName ? el.tagName.toLowerCase() : '')
  }
  const findBySel = (sel) => { try { return document.querySelector(sel) } catch (e) { return null } }
  const label = (el) => {
    const dp = el.closest('[data-path]')                       // 코드 지도 노드면 파일 경로가 제일 좋은 라벨
    if (dp) return '파일: ' + dp.getAttribute('data-path')
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ')
    return `${el.tagName.toLowerCase()}${t ? ' ↳ ' + t.slice(0, 40) : ''}`.slice(0, 120)
  }
  const anchor = (el) => ((el.textContent || '').trim().replace(/\s+/g, ' ') || el.tagName.toLowerCase()).slice(0, 160)

  /* ── 상태·UI ── */
  let mode = false
  let items = []
  const root = document.createElement('div')
  root.setAttribute('data-fb-ui', '')
  root.innerHTML = `
  <style>
  #fbToggle{position:fixed;right:16px;bottom:16px;z-index:60;width:44px;height:44px;border-radius:50%;border:1px solid #26262f;background:#16161d;color:#b9b9c6;font-size:18px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.5)}
  #fbToggle.on{border-color:#7b78f0;color:#7b78f0}
  #fbHl{position:fixed;z-index:55;pointer-events:none;border:1.5px solid #7b78f0;border-radius:4px;background:rgba(123,120,240,.08);display:none}
  .fbPin{position:fixed;z-index:56;width:22px;height:22px;border-radius:50% 50% 50% 4px;background:#7b78f0;color:#fff;font:700 11px system-ui;display:flex;align-items:center;justify-content:center;cursor:pointer;transform:translate(-4px,-20px);box-shadow:0 2px 8px rgba(0,0,0,.5)}
  .fbPin.acted{background:#3f8f5f}
  .fbPop{position:fixed;z-index:57;width:280px;background:#0d0d12;border:1px solid #7b78f0;border-radius:10px;padding:10px;color:#e8e8ef;font:12.5px/1.5 system-ui}
  .fbPop textarea{width:100%;height:64px;background:#16161d;border:1px solid #26262f;border-radius:6px;color:#e8e8ef;font:12.5px/1.5 system-ui;padding:6px;box-sizing:border-box}
  .fbPop input{width:100%;background:#16161d;border:1px solid #26262f;border-radius:6px;color:#e8e8ef;font:12px system-ui;padding:5px 8px;box-sizing:border-box}
  .fbPop .row{display:flex;gap:6px;justify-content:flex-end;margin-top:6px}
  .fbPop button{padding:5px 10px;font-size:12px;background:#16161d;border:1px solid #26262f;border-radius:7px;color:#b9b9c6;cursor:pointer}
  .fbPop button.pri{border-color:#7b78f0;color:#7b78f0}
  .fbPop .muted{color:#8b8b99;font-size:10.5px}
  .fbPop .cm{border-top:1px solid #26262f;padding:4px 0;font-size:12px}
  </style>
  <button id="fbToggle" title="피드백 모드 — 아무 요소나 클릭해 핀">📌</button>
  <div id="fbHl"></div>
  <div id="fbPins"></div>
  <div id="fbDraft" class="fbPop" style="display:none"></div>
  <div id="fbThread" class="fbPop" style="display:none"></div>`
  document.body.appendChild(root)
  const $ = (s) => root.querySelector(s)
  const tg = $('#fbToggle'), hl = $('#fbHl'), pinBox = $('#fbPins'), draft = $('#fbDraft'), thread = $('#fbThread')

  /* ── 핀 표시(전 상태 — 완료만 제외) ── */
  async function load() {
    try { items = ((await jget(`${API}?service=${SERVICE}&route=${encodeURIComponent(ROUTE)}`)).items || []).filter((i) => i.status !== 'closed') }
    catch (e) { items = [] }
    place()
  }
  function place() {
    pinBox.innerHTML = ''
    if (!mode && thread.style.display === 'none') return
    items.forEach((it, i) => {
      const el = findBySel(it.selector)
      if (!el) return
      const r = el.getBoundingClientRect()
      if (!r.width && !r.height) return
      const d = document.createElement('div')
      d.className = 'fbPin' + (it.status !== 'open' ? ' acted' : '')
      d.style.left = r.left + (r.width * (parseFloat(it.pos_x) || 0)) / 100 + 'px'
      d.style.top = r.top + (r.height * (parseFloat(it.pos_y) || 0)) / 100 + 'px'
      d.textContent = i + 1
      d.title = it.body
      d.onclick = (ev) => { ev.stopPropagation(); openThread(it, ev) }
      pinBox.appendChild(d)
    })
  }
  let rafT = 0
  const refresh = () => { cancelAnimationFrame(rafT); rafT = requestAnimationFrame(place) }
  window.addEventListener('scroll', refresh, true)
  window.addEventListener('resize', refresh)
  window.FBPins = { refresh, reload: load }                    // 페이지(팬/줌 등)가 재측정을 부를 수 있게

  /* ── 모드: 요소 피킹 ── */
  tg.onclick = () => { mode = !mode; tg.classList.toggle('on', mode); hl.style.display = 'none'; place() }
  document.addEventListener('mousemove', (e) => {
    if (!mode) return
    const el = pickable(e.target)
    if (!el) { hl.style.display = 'none'; return }
    const r = el.getBoundingClientRect()
    Object.assign(hl.style, { display: 'block', left: r.left - 2 + 'px', top: r.top - 2 + 'px', width: r.width + 'px', height: r.height + 'px' })
  }, true)
  function pickable(t) {
    if (!t || t.nodeType !== 1) return null
    if (t.closest('[data-fb-ui]')) return null
    if (t === document.body || t === document.documentElement) return null
    return t
  }
  document.addEventListener('click', (e) => {
    if (!mode) return
    const el = pickable(e.target)
    if (!el) return
    e.preventDefault(); e.stopPropagation()
    openDraft(el, e)
  }, true)

  /* ── 작성 팝오버 ── */
  function popAt(box, ev) {
    box.style.display = 'block'
    box.style.left = Math.min(ev.clientX, window.innerWidth - 300) + 'px'
    box.style.top = Math.min(ev.clientY + 10, window.innerHeight - 220) + 'px'
  }
  function openDraft(el, ev) {
    const r = el.getBoundingClientRect()
    const payload = {
      service: SERVICE, route: ROUTE, selector: buildSelector(el),
      element_label: label(el), anchor_text: anchor(el),
      pos_x: (((ev.clientX - r.left) / (r.width || 1)) * 100).toFixed(2),
      pos_y: (((ev.clientY - r.top) / (r.height || 1)) * 100).toFixed(2),
    }
    draft.innerHTML = `<div class="muted">${payload.element_label.replace(/</g, '&lt;')}</div>
      <textarea placeholder="피드백…"></textarea>
      <div class="row"><button data-x>취소</button><button class="pri" data-s>핀 저장</button></div>`
    popAt(draft, ev)
    const ta = draft.querySelector('textarea'); ta.focus()
    draft.querySelector('[data-x]').onclick = () => (draft.style.display = 'none')
    draft.querySelector('[data-s]').onclick = async () => {
      const body = ta.value.trim()
      if (!body) return
      try { await jpost(API, { ...payload, body }); draft.style.display = 'none'; load() }
      catch (e) { alert('핀 저장 실패: ' + e.message + ' (admin 토큰 확인)') }
    }
  }

  /* ── 스레드 팝오버 ── */
  async function openThread(it, ev) {
    thread.innerHTML = '로딩…'; popAt(thread, ev)
    let d = it
    try { d = await jget(API + it.id + '/') } catch (e) { /* 목록 데이터로 표시 */ }
    thread.innerHTML = `<div class="muted">#${d.id} · ${(d.author || '').replace(/</g, '&lt;')} · ${(d.status_label || d.status || '').replace(/</g, '&lt;')}</div>
      <div style="white-space:pre-wrap;margin:4px 0 6px">${(d.body || '').replace(/</g, '&lt;')}</div>
      ${(d.comments || []).map((c) => `<div class="cm"><span class="muted">${(c.author || '').replace(/</g, '&lt;')}</span> ${(c.body || '').replace(/</g, '&lt;')}</div>`).join('')}
      ${d.resolution ? `<div class="cm"><span class="muted">처리 노트</span> ${(d.resolution || '').replace(/</g, '&lt;')}</div>` : ''}
      <input placeholder="댓글(Enter)…" />
      <div class="row"><a class="muted" href="${API.replace(/api\/feedback\/$/, '')}" style="margin-right:auto">백로그 →</a><button data-x>닫기</button></div>`
    thread.querySelector('[data-x]').onclick = () => { thread.style.display = 'none'; place() }
    thread.querySelector('input').addEventListener('keydown', async (e) => {
      if (e.key === 'Enter' && e.target.value.trim()) { await jpost(API + d.id + '/comments/', { body: e.target.value.trim() }); openThread(it, ev) }
    })
  }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('[data-fb-ui]') && !e.target.closest('.fbPin')) thread.style.display = 'none'
  })

  load()
})()
