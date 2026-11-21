/* ClipTool full-video view: watch the whole source video, search its transcript and cut your own clips.
 *
 * Uses globals from index.html: $, esc, fmt, toast, currentFolder, openRun, releaseVideos, setBusy, showJob, poll.
 */
(() => {
  let F = null;  // { folder, video, clips, sentences, wave, a, b, snap }
  const el = id => document.getElementById(id);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const hms = t => { t = Math.max(0, t); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = (t % 60).toFixed(1);
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + s.padStart(4, "0"); };

  function build(){
    if (el("fv")) return;
    const d = document.createElement("div");
    d.id = "fv"; d.className = "fv"; d.hidden = true;
    d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-label", "Full video");
    d.innerHTML = `
      <header class="fv-top">
        <button type="button" data-fv="close" title="Back to clips (Esc)">← Clips</button>
        <div class="fv-title"><b id="fv-title"></b><span id="fv-sub"></span></div>
      </header>
      <div class="fv-main">
        <section class="fv-left">
          <video id="fv-video" controls playsinline preload="metadata"></video>
          <div class="fv-bar">
            <canvas id="fv-wave" aria-hidden="true"></canvas>
            <div id="fv-clips"></div>
            <div class="fv-sel" id="fv-sel" hidden></div>
            <div class="fv-ph" id="fv-ph"></div>
          </div>
          <div class="fv-cliprow" id="fv-cliprow" hidden></div>
          <div class="fv-legend"><span class="lg c"></span> your clips <span class="lg s"></span> new selection · click to jump, drag to select</div>
          <div class="fv-cut">
            <div class="fv-io">
              <button type="button" data-fv="in" title="Set start at the playhead (I)">⟦ Start here <kbd>I</kbd></button>
              <span class="fv-t" id="fv-a">–</span>
              <span class="fv-arrow">→</span>
              <span class="fv-t" id="fv-b">–</span>
              <button type="button" data-fv="out" title="Set end at the playhead (O)">End here ⟧ <kbd>O</kbd></button>
              <span class="fv-len" id="fv-len"></span>
            </div>
            <div class="fv-io">
              <label class="fv-snap"><input type="checkbox" id="fv-snap" checked> Snap to sentences</label>
              <button type="button" data-fv="nudge" data-k="a" data-v="-0.5">Start −0.5s</button>
              <button type="button" data-fv="nudge" data-k="a" data-v="0.5">Start +0.5s</button>
              <button type="button" data-fv="nudge" data-k="b" data-v="-0.5">End −0.5s</button>
              <button type="button" data-fv="nudge" data-k="b" data-v="0.5">End +0.5s</button>
              <button type="button" data-fv="playsel" title="Play the selection (P)">▶ Play selection <kbd>P</kbd></button>
            </div>
            <div class="fv-io">
              <input id="fv-name" maxlength="100" placeholder="Clip title (filled in from what's said)" aria-label="Clip title">
              <select id="fv-fav" class="fv-favsel" aria-label="Also save the new clip to a favorites folder" title="Also save the new clip to one of your favorites folders when it's ready"></select>
              <button type="button" class="primary" data-fv="create">✂ Create clip</button>
            </div>
            <p class="fv-msg" id="fv-msg"></p>
          </div>
        </section>
        <aside class="fv-right">
          <input id="fv-q" type="search" placeholder="Search what's said… (e.g. tiger, cash)" aria-label="Search the transcript">
          <div class="fv-count" id="fv-count"></div>
          <div class="fv-list" id="fv-list" role="list"></div>
        </aside>
      </div>`;
    document.body.appendChild(d);
    wire();
  }

  // ---------------------------------------------------------------- rendering
  function pct(t){ return (t / F.video.duration * 100) + "%"; }
  function drawWave(){
    const c = el("fv-wave"), bar = c.parentElement, dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(bar.clientWidth * dpr), H = Math.round(bar.clientHeight * dpr);
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    // Most-replayed heatmap (if YouTube has it) as a soft area, waveform on top.
    if (F.heatmap.length) {
      ctx.fillStyle = "rgba(236,72,153,.18)"; ctx.beginPath(); ctx.moveTo(0, H);
      for (const h of F.heatmap) ctx.lineTo(h.start_time / F.video.duration * W, H - h.value * H * 0.9);
      ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
    }
    if (!F.wave) return;
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--quiet") || "#888"; ctx.globalAlpha = .6;
    const pk = F.wave.peaks;
    for (let x = 0; x < W; x++) {
      const v = pk[Math.floor(x / W * pk.length)] || 0, h = Math.max(1, v * (H - 6));
      ctx.fillRect(x, (H - h) / 2, 1, h);
    }
  }
  /* When the clip finishes rendering, refresh the markers (the placeholder gets its real number). */
  function watchJob(id, favFolder){
    const folder = F.folder;
    const tick = async () => {
      if (!F || F.folder !== folder) return;
      const j = await fetch(`/api/jobs/${id}`).then(r => r.ok ? r.json() : null).catch(() => null);
      if (j && (j.status === "queued" || j.status === "running")) return setTimeout(tick, 2000);
      const o = await fetch(`/api/runs/${encodeURIComponent(folder)}/overview`).then(r => r.ok ? r.json() : null).catch(() => null);
      if (!F || F.folder !== folder) return;
      if (o) { F.clips = o.clips; renderClips(); }
      el("fv-msg").textContent = j && j.status === "done" ? "✓ Clip ready - it's in the clip list (Picked by you)." : `Clip failed: ${(j && j.error) || "unknown error"}`;
      if (j && j.status === "done" && favFolder && j.result && j.result.rank != null) {
        try { await Fav.save(folder, [j.result.rank], favFolder); el("fv-msg").textContent += ` Saved to “${favFolder}”.`; }
        catch (err) { toast(err.message); }
      }
    };
    setTimeout(tick, 2000);
  }
  /* Clicked a clip marker: show it with "★ Save to folder". */
  function showClipRow(rank){
    const c = F.clips.find(x => x.rank === rank), row = el("fv-cliprow");
    if (!c || typeof c.rank !== "number") { row.hidden = true; return; }
    row.innerHTML = `<b>#${c.rank}</b> <span>${esc(c.title)}</span> <span class="favnote">${fmt(c.start)}–${fmt(c.end)}</span>
      <button type="button" data-fvplay="${c.rank}">▶ Play</button>
      <button type="button" class="favgo" data-fvfav="${c.rank}" data-favpick>★ Save to folder</button>`;
    row.hidden = false;
  }
  /* Folder choice for the next clip you create. */
  async function fillFavSelect(){
    const sel = el("fv-fav"); if (!sel || !window.Fav) return;
    const keep = sel.value;
    const list = await Fav.refresh();
    sel.innerHTML = `<option value="">Don't save to a folder</option>` + list.map(f => `<option value="${esc(f.name)}">★ Save to: ${esc(f.name)}</option>`).join("")
      + `<option value="__new">★ Save to a new folder…</option>`;
    sel.value = list.some(f => f.name === keep) ? keep : "";
  }
  function renderClips(){
    el("fv-clips").innerHTML = F.clips.map(c =>
      `<button type="button" class="fv-clip${c.manual ? " manual" : ""}" data-seek="${c.start}" data-cliprank="${c.rank}" style="left:${pct(c.start)};width:${Math.max(0.25, (c.end - c.start) / F.video.duration * 100)}%"
        title="#${c.rank} ${esc(c.title)} (${fmt(c.start)}–${fmt(c.end)})">${c.rank}</button>`).join("");
  }
  function renderSel(){
    const s = el("fv-sel");
    const has = F.a != null && F.b != null && F.b > F.a;
    s.hidden = !has;
    if (has) { s.style.left = pct(F.a); s.style.width = ((F.b - F.a) / F.video.duration * 100) + "%"; }
    el("fv-a").textContent = F.a == null ? "–" : hms(F.a);
    el("fv-b").textContent = F.b == null ? "–" : hms(F.b);
    const len = has ? F.b - F.a : 0;
    el("fv-len").textContent = has ? `${len.toFixed(1)} s` + (len > 60 ? " · longer than a Short (60 s)" : "") : "";
    el("fv-len").classList.toggle("warn", len > 60 || (has && len < 3));
    document.querySelector('#fv [data-fv="create"]').disabled = !(has && len >= 3 && len <= 180);
    if (has && !F.nameTouched) el("fv-name").value = suggestTitle();
  }
  function suggestTitle(){
    const lines = F.sentences.filter(s => s.end > F.a && s.start < F.b).map(s => s.text.replace(/>>/g, "").trim()).filter(Boolean);
    const best = lines.find(l => /\?|!/.test(l) && l.split(" ").length >= 3) || lines.find(l => l.split(" ").length >= 4) || lines[0] || "";
    return best.length > 60 ? best.slice(0, 57).replace(/\s+\S*$/, "") + "..." : best;
  }
  function renderList(){
    const q = el("fv-q").value.trim().toLowerCase();
    const rows = F.sentences.map((s, i) => [s, i]).filter(([s]) => !q || s.text.toLowerCase().includes(q));
    el("fv-count").textContent = q ? `${rows.length} match${rows.length === 1 ? "" : "es"}` : `${F.sentences.length} lines`;
    const hi = t => q ? esc(t).replace(new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), m => `<mark>${m}</mark>`) : esc(t);
    el("fv-list").innerHTML = rows.slice(0, 1500).map(([s, i]) =>
      `<div class="fv-line" role="listitem" data-i="${i}"><button type="button" class="fv-time" data-seek="${s.start}">${fmt(s.start)}</button>
        <span class="fv-text" data-seek="${s.start}">${hi(s.text)}</span>
        <span class="fv-acts"><button type="button" data-from="${i}" title="Start the clip at this line">Start</button><button type="button" data-to="${i}" title="End the clip after this line">End</button></span></div>`).join("");
  }
  function followPlayhead(){
    const v = el("fv-video");
    el("fv-ph").style.left = pct(v.currentTime || 0);
    const i = F.sentences.findIndex(s => v.currentTime >= s.start && v.currentTime < s.end + 0.3);
    if (i !== F.cur) {
      F.cur = i;
      el("fv-list").querySelectorAll(".fv-line.now").forEach(n => n.classList.remove("now"));
      const n = el("fv-list").querySelector(`[data-i="${i}"]`);
      if (n) { n.classList.add("now"); if (!v.paused && !F.listHover) n.scrollIntoView({ block: "nearest" }); }
    }
    if (F.playSel && v.currentTime >= F.b) { v.pause(); F.playSel = false; }
  }

  // ---------------------------------------------------------------- selection
  function snapTo(t, edge){
    if (!el("fv-snap").checked) return +t.toFixed(2);
    const near = F.sentences.map(s => edge === "a" ? s.start : s.end).reduce((best, x) => Math.abs(x - t) < Math.abs(best - t) ? x : best, Infinity);
    return Math.abs(near - t) <= 2.0 ? +near.toFixed(2) : +t.toFixed(2);
  }
  function setA(t){ F.a = clamp(snapTo(t, "a"), 0, F.video.duration - 1); if (F.b != null && F.b <= F.a) F.b = null; renderSel(); }
  function setB(t){ F.b = clamp(snapTo(t, "b"), 0, F.video.duration); if (F.a != null && F.b <= F.a) F.a = null; renderSel(); }

  async function create(){
    const btn = document.querySelector('#fv [data-fv="create"]');
    btn.disabled = true;
    el("fv-msg").textContent = "";
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(F.folder)}/clips`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: F.a, end: F.b, title: el("fv-name").value.trim() }) });
      const d = await r.json();
      if (!r.ok) { el("fv-msg").textContent = d.error || "Couldn't create the clip."; return; }
      F.clips.push({ rank: "…", start: F.a, end: F.b, title: el("fv-name").value || "New clip", manual: true }); renderClips();
      toast(d.status === "queued" && d.stage.startsWith("Waiting") ? "Clip queued - it renders after the current job" : "Making your clip - it will appear in the clip list");
      el("fv-msg").textContent = "✓ Clip is rendering. Keep picking more, or go back to the clip list.";
      F.a = F.b = null; F.nameTouched = false; el("fv-name").value = ""; renderSel();
      setBusy(true); showJob({ status: d.status, stage: d.stage, progress: 0.05, log: [] }); poll(d.id);
      watchJob(d.id, el("fv-fav").value && el("fv-fav").value !== "__new" ? el("fv-fav").value : null);
    } finally { renderSel(); }
  }

  // ---------------------------------------------------------------- wiring
  function wire(){
    const root = el("fv"), v = el("fv-video"), bar = document.querySelector("#fv .fv-bar");
    root.addEventListener("click", e => {
      const s = e.target.closest("[data-seek]");
      if (s) { v.currentTime = +s.dataset.seek; if (s.dataset.cliprank) showClipRow(+s.dataset.cliprank); return; }
      const fs = e.target.closest("[data-fvfav]");
      if (fs) { const rank = +fs.dataset.fvfav; Fav.pick(fs, { ranks: [rank], label: `Save clip #${rank} to…`, now: { folder: F.folder, ranks: [rank] } }); return; }
      const pl = e.target.closest("[data-fvplay]");
      if (pl) { const c = F.clips.find(x => x.rank === +pl.dataset.fvplay); if (c) { setA(c.start); setB(c.end); v.currentTime = c.start; F.playSel = true; v.play(); } return; }
      const f = e.target.closest("[data-from]"); if (f) { setA(F.sentences[+f.dataset.from].start); v.currentTime = F.a; return; }
      const t = e.target.closest("[data-to]"); if (t) { setB(F.sentences[+t.dataset.to].end); return; }
      const b = e.target.closest("[data-fv]"); if (!b || b.disabled) return;
      const act = b.dataset.fv;
      if (act === "close") close();
      if (act === "in") setA(v.currentTime);
      if (act === "out") setB(v.currentTime);
      if (act === "nudge") { const k = b.dataset.k; if (F[k] != null) { F[k] = +(F[k] + +b.dataset.v).toFixed(2); if (F.b <= F.a) F[k] -= +b.dataset.v; renderSel(); } }
      if (act === "playsel" && F.a != null) { v.currentTime = F.a; F.playSel = F.b != null; v.play(); }
      if (act === "create") create();
    });
    el("fv-name").addEventListener("input", () => { F.nameTouched = true; });
    el("fv-fav").addEventListener("change", async e => {
      if (e.target.value !== "__new") return;
      const n = prompt("Name of the new favorites folder:");
      if (!n || !n.trim()) { e.target.value = ""; return; }
      try {
        const r = await fetch("/api/favs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: n }) });
        const d = await r.json(); if (!r.ok) throw new Error(d.error);
        await fillFavSelect(); e.target.value = d.name; toast(`Folder “${d.name}” created`);
      } catch (err) { toast(err.message || "Couldn't create the folder"); e.target.value = ""; }
    });
    el("fv-q").addEventListener("input", renderList);
    el("fv-list").addEventListener("pointerenter", () => { F.listHover = true; });
    el("fv-list").addEventListener("pointerleave", () => { F.listHover = false; });
    v.addEventListener("timeupdate", followPlayhead);
    v.addEventListener("seeked", followPlayhead);
    window.addEventListener("resize", () => { if (F && !el("fv").hidden) drawWave(); });

    // Timeline: click to seek, drag to select a range.
    let drag = null;
    const tAt = e => { const r = bar.getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width, 0, 1) * F.video.duration; };
    bar.addEventListener("pointerdown", e => { if (e.target.closest(".fv-clip")) return; bar.setPointerCapture(e.pointerId); drag = { t0: tAt(e), moved: false }; });
    bar.addEventListener("pointermove", e => {
      if (!drag) return;
      const t = tAt(e);
      if (Math.abs(t - drag.t0) / F.video.duration > 0.002) drag.moved = true;
      if (drag.moved) { F.a = Math.min(drag.t0, t); F.b = Math.max(drag.t0, t); renderSel(); }
    });
    bar.addEventListener("pointerup", e => {
      if (!drag) return;
      if (!drag.moved) v.currentTime = drag.t0;
      else { setA(F.a); setB(F.b); v.currentTime = F.a; }
      drag = null;
    });

    document.addEventListener("keydown", e => {
      if (!F || el("fv").hidden) return;
      if (e.target.closest("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
      const k = e.key.toLowerCase();
      if (k === "i") { e.preventDefault(); setA(v.currentTime); }
      else if (k === "o") { e.preventDefault(); setB(v.currentTime); }
      else if (k === "p") { e.preventDefault(); document.querySelector('#fv [data-fv="playsel"]').click(); }
      else if (k === " ") { e.preventDefault(); v.paused ? v.play() : v.pause(); }
      else if (k === "arrowleft" || k === "arrowright") { e.preventDefault(); v.currentTime += (k === "arrowleft" ? -1 : 1) * (e.shiftKey ? 10 : 2); }
      else if (k === "escape") close();
    });
  }

  async function open(folder){
    build();
    const r = await fetch(`/api/runs/${encodeURIComponent(folder)}/overview`);
    const d = await r.json();
    if (!r.ok) { toast(d.error || "Can't open the full video."); return; }
    F = { folder, video: d.video, clips: d.clips, sentences: d.sentences, heatmap: d.heatmap, wave: null, a: null, b: null, cur: -1, nameTouched: false };
    el("fv-title").textContent = d.video.title;
    el("fv-sub").textContent = ` · ${d.video.channel || ""} · ${fmt(d.video.duration || 0)} · ${d.clips.length} clips`;
    el("fv-msg").textContent = ""; el("fv-q").value = ""; el("fv-name").value = "";
    const v = el("fv-video"); v.src = d.source_url;
    el("fv").hidden = false; document.body.style.overflow = "hidden";
    renderClips(); renderSel(); renderList(); drawWave();
    el("fv-cliprow").hidden = true; fillFavSelect();
    fetch(`/api/runs/${encodeURIComponent(folder)}/overview-wave`).then(r => r.ok ? r.json() : null).then(w => { if (w && F) { F.wave = w; drawWave(); } });
  }
  function close(){
    const v = el("fv-video"); v.pause(); v.removeAttribute("src"); v.load();
    el("fv").hidden = true; document.body.style.overflow = "";
    if (F) openRun(F.folder);
  }
  window.openFullVideo = open;
})();
