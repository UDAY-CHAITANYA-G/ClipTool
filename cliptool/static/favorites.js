/* ClipTool favorites: folders of stock footage saved from the full video, and a video maker that joins
 * several of them into one video (which then opens in the full editor).
 *
 * Uses globals from index.html: $, esc, fmt, toast, poll, openRun, loadLibrary, openEditor, releaseVideos.
 * Exposes window.Fav = { pick, saveRange, open, folders, refresh, onJobDone }.
 */
(() => {
  let FOLDERS = [];
  let CUR = null;                     // folder shown in the viewer
  let ITEMS = [];                     // its items
  let SEQ = [];                       // the video being made: [{fav, key, title, thumb, file, dur, in, out}]
  let MAKER = false;                  // video maker panel open
  let PRESETS = null;
  const api = (u, o) => fetch(u, o).then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || "Something went wrong"); return d; });
  const post = (u, body) => api(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const enc = encodeURIComponent;
  const fileUrl = (folder, f) => `/favfile/${enc(folder)}/${enc(f)}`;
  const copy = async t => { try { await navigator.clipboard.writeText(t); toast("Copied"); } catch { toast("Couldn't copy"); } };
  const secs = t => (t < 60 ? t.toFixed(1) + " s" : fmt(t));
  try { SEQ = JSON.parse(localStorage.getItem("cliptool-seq") || "[]"); } catch { SEQ = []; }
  const keepSeq = () => { try { localStorage.setItem("cliptool-seq", JSON.stringify(SEQ)); } catch {} };

  async function folders(){
    try { FOLDERS = (await api("/api/favs")).folders || []; } catch { FOLDERS = []; }
    const n = FOLDERS.reduce((a, f) => a + f.count, 0);
    const b = $("open-favs"); if (b) b.querySelector(".favn").textContent = n ? String(n) : "";
    return FOLDERS;
  }

  // ------------------------------------------------------------------ folder picker pop-up
  function pop(){
    let p = $("favpop");
    if (!p) {
      p = document.createElement("div");
      p.id = "favpop"; p.className = "favpop"; p.hidden = true;
      p.setAttribute("role", "dialog"); p.setAttribute("aria-label", "Save to folder");
      document.body.appendChild(p);
      document.addEventListener("click", e => { if (!p.hidden && !e.target.closest("#favpop, [data-favpick]")) p.hidden = true; });
      document.addEventListener("keydown", e => { if (e.key === "Escape" && !p.hidden) { p.hidden = true; e.stopPropagation(); } }, true);
    }
    return p;
  }
  /* Ask which folder; resolves with {name, create} (or nothing if closed). */
  function pick(anchor, label){
    return new Promise(async resolve => {
      const p = pop();
      await folders();
      p.innerHTML = `
        <h3>${esc(label || "Save to…")}</h3>
        ${FOLDERS.length ? `<div class="favlist">${FOLDERS.map((f, i) => `<button type="button" data-i="${i}"><span>📁 ${esc(f.name)}</span><small>${f.count}</small></button>`).join("")}</div>`
          : `<p class="favnote">No folders yet - make your first one:</p>`}
        <div class="favnew"><input id="favnew-name" maxlength="60" placeholder="New folder, e.g. Funny moments" aria-label="New folder name">
          <button type="button" class="primary" data-new>Create &amp; save</button></div>`;
      const r = anchor.getBoundingClientRect();
      p.hidden = false;
      const w = p.offsetWidth, h = p.offsetHeight;
      p.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
      p.style.top = (r.bottom + h + 8 < window.innerHeight ? r.bottom + 6 : Math.max(8, r.top - h - 6)) + "px";
      const done = v => { p.hidden = true; resolve(v); };
      p.onclick = e => {
        const b = e.target.closest("[data-i]");
        if (b) return done({ name: FOLDERS[+b.dataset.i].name, create: false });
        if (e.target.closest("[data-new]")) {
          const n = $("favnew-name").value.trim();
          if (!n) { $("favnew-name").focus(); return; }
          done({ name: n, create: true });
        }
      };
      $("favnew-name").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); p.querySelector("[data-new]").click(); } };
      (FOLDERS.length ? p.querySelector("[data-i]") : $("favnew-name")).focus();
    });
  }

  /* Full video view: save a stretch of the original video (no captions / logos) into a folder. */
  async function saveRange(anchor, runFolder, start, end, title){
    if (end - start < 1) { toast("Select at least 1 second."); return; }
    const to = await pick(anchor, `Save ${secs(end - start)} of the video to…`);
    if (!to) return;
    try {
      const d = await post(`/api/favs/${enc(to.name)}/add-range`, { folder: runFolder, start, end, title: title || "", create: to.create });
      toast(`Saving ${secs(end - start)} to “${to.name}” - it's in the job list`);
      poll(d.id);
    } catch (e) { toast(e.message); }
  }

  /* Called by the page when a job finishes. */
  async function onJobDone(j){
    if (j.kind === "fav") {
      await folders();
      if ($("favview") && !$("favview").hidden) { renderFolders(); if (CUR) showFolder(CUR); }
    }
    if (j.kind === "montage" && j.status === "done" && j.folder) {
      if ($("favview") && !$("favview").hidden) close();
      await loadLibrary(); await openRun(j.folder);
      await openEditor(j.folder, 1);
      toast("Your video is ready - style it here (layout, captions, logos, text, cut...) and Save & render");
    }
  }

  // ------------------------------------------------------------------ viewer
  function view(){
    let v = $("favview");
    if (v) return v;
    v = document.createElement("div");
    v.id = "favview"; v.className = "favview"; v.hidden = true;
    v.setAttribute("role", "dialog"); v.setAttribute("aria-modal", "true"); v.setAttribute("aria-label", "Favorites");
    v.innerHTML = `
      <header class="fav-top">
        <button type="button" data-fa="close" title="Back (Esc)">← Back</button>
        <h2>★ Favorites</h2>
        <span class="fav-root" id="fav-root"></span>
        <button type="button" class="mkbtn" data-fa="maker" aria-pressed="false" title="Join several saved clips into one video">🎬 Make a video <span class="mkn" id="mk-n"></span></button>
      </header>
      <div class="fav-main" id="fav-main">
        <aside class="fav-side">
          <div class="favnew"><input id="fav-newname" maxlength="60" placeholder="New folder name" aria-label="New folder name">
            <button type="button" data-fa="new">+ New</button></div>
          <nav class="fav-folders" id="fav-folders" aria-label="Your folders"></nav>
          <button type="button" class="fav-rootbtn" data-fa="openroot" title="Open the Favorites folder in Explorer">Open Favorites in Explorer</button>
        </aside>
        <section class="fav-content" id="fav-content" aria-live="polite"></section>
        <aside class="maker" id="maker" hidden aria-label="Make a video"></aside>
      </div>`;
    document.body.appendChild(v);
    v.addEventListener("click", onClick);
    v.addEventListener("change", onChange);
    v.addEventListener("dragstart", e => { const r = e.target.closest("[data-seqi]"); if (r) { e.dataTransfer.setData("text/plain", r.dataset.seqi); r.classList.add("dragging"); } });
    v.addEventListener("dragend", e => { const r = e.target.closest("[data-seqi]"); if (r) r.classList.remove("dragging"); });
    v.addEventListener("dragover", e => { if (e.target.closest("[data-seqi]")) e.preventDefault(); });
    v.addEventListener("drop", e => {
      const r = e.target.closest("[data-seqi]"); if (!r) return;
      e.preventDefault();
      const from = +e.dataTransfer.getData("text/plain"), to = +r.dataset.seqi;
      if (Number.isNaN(from) || from === to) return;
      const [x] = SEQ.splice(from, 1); SEQ.splice(to, 0, x); keepSeq(); renderMaker();
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !v.hidden && $("favpop")?.hidden !== false) close(); });
    $("fav-newname").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); v.querySelector('[data-fa="new"]').click(); } });
    return v;
  }
  function stopVideos(){
    const v = $("favview"); if (!v) return;
    v.querySelectorAll("video").forEach(x => { x.pause(); x.removeAttribute("src"); x.load(); });
    MK.playing = false;
  }
  async function open(name){
    if (typeof releaseVideos === "function") releaseVideos();
    const v = view(); v.hidden = false; document.body.style.overflow = "hidden";
    await folders();
    try { $("fav-root").textContent = (await api("/api/favs")).root; } catch {}
    renderFolders(); renderMaker();
    const pickName = name || CUR || (FOLDERS[0] && FOLDERS[0].name);
    if (pickName && FOLDERS.some(f => f.name === pickName)) showFolder(pickName); else showEmpty();
  }
  function close(){ stopVideos(); $("favview").hidden = true; document.body.style.overflow = ""; }
  function renderFolders(){
    $("fav-folders").innerHTML = FOLDERS.length ? FOLDERS.map(f => `
      <button type="button" class="fav-f${f.name === CUR ? " sel" : ""}" data-fa="folder" data-name="${esc(f.name)}" aria-current="${f.name === CUR}">
        ${f.cover ? `<img src="/favfile/${f.cover.split("/").map(enc).join("/")}" alt="">` : `<span class="fav-ico">📁</span>`}
        <span class="fav-fn"><b>${esc(f.name)}</b><small>${f.count} clip${f.count === 1 ? "" : "s"} · ${f.size_mb} MB</small></span>
      </button>`).join("") : `<p class="favnote">No folders yet. In a run, open <b>▶ Full video</b>, select a part and press <b>★ Save selection to folder</b>.</p>`;
  }
  function showEmpty(){
    CUR = null; ITEMS = []; renderFolders();
    $("fav-content").innerHTML = `<div class="fav-empty"><p>Pick a folder on the left, or create one.</p>
      <p class="favnote">To save clips: open a run, press <b>▶ Full video – make clips</b>, select a part and press <b>★ Save selection to folder</b>.</p></div>`;
  }
  async function showFolder(name){
    stopVideos();
    CUR = name; renderFolders();
    let d;
    try { d = await api(`/api/favs/${enc(name)}`); } catch (e) { toast(e.message); await folders(); renderFolders(); return showEmpty(); }
    ITEMS = d.items;
    const f = FOLDERS.find(x => x.name === name) || { size_mb: 0 };
    const inSeq = k => SEQ.filter(s => s.fav === d.name && s.key === k).length;
    $("fav-content").innerHTML = `
      <div class="fav-head">
        <div><h3>📁 ${esc(d.name)}</h3><p class="favnote">${d.items.length} clip${d.items.length === 1 ? "" : "s"} · ${f.size_mb} MB · <span class="fav-path">${esc(d.path)}</span></p></div>
        <div class="fav-tools">
          ${d.items.length ? `<button type="button" class="mkadd" data-fa="addall">+ Add all to video</button>` : ""}
          <a class="btnlike" href="/api/favs/${enc(d.name)}/export.xlsx" download title="No., title, description of every clip in this folder">⬇ List (.xlsx)</a>
          <button type="button" data-fa="openf">Open in Explorer</button>
          <button type="button" data-fa="rename">Rename</button>
          <button type="button" class="danger" data-fa="delf">Delete folder</button>
        </div>
      </div>
      ${d.items.length ? `<div class="fav-grid">${d.items.map((i, n) => `
        <article class="fav-card${i.kind === "stock" ? " stock" : ""}">
          <video controls preload="metadata" playsinline ${i.thumb ? `poster="${fileUrl(d.name, i.thumb)}"` : ""} src="${fileUrl(d.name, i.file)}" aria-label="${esc(i.title)}"></video>
          <div class="fav-body">
            <h4 title="${esc(i.title)}">${esc(i.title)}</h4>
            <p class="favnote" title="${esc(i.video_title || "")}">${secs(i.duration || 0)} · ${esc(i.channel || "")}${i.source_url ? ` · <a href="${esc(i.source_url)}" target="_blank" rel="noopener">${fmt(i.start)}</a>` : ""}</p>
            <div class="fav-acts">
              <button type="button" class="mkadd" data-fa="add" data-n="${n}" title="Add to the video you're making">+ Add to video${inSeq(i.key) ? ` (${inSeq(i.key)})` : ""}</button>
              <a href="${fileUrl(d.name, i.file)}?download=1" title="Download MP4" aria-label="Download">⬇</a>
              <button type="button" data-fa="ctitle" data-n="${n}" title="Copy title">Title</button>
              <button type="button" class="danger" data-fa="remove" data-n="${n}" title="Remove from this folder" aria-label="Remove ${esc(i.title)}">✕</button>
            </div>
          </div>
        </article>`).join("")}</div>`
        : `<div class="fav-empty"><p>This folder is empty.</p><p class="favnote">Open a run's <b>▶ Full video</b>, select a part and press <b>★ Save selection to folder</b>.</p></div>`}`;
  }

  // ------------------------------------------------------------------ video maker
  const MK = { playing: false, i: 0 };
  const seqLen = () => SEQ.reduce((t, s) => t + Math.max(0, s.out - s.in), 0);
  function addToSeq(item, folder){
    const dur = +(item.duration || (item.end - item.start) || 0);
    SEQ.push({ fav: folder, key: item.key, title: item.title, thumb: item.thumb, file: item.file, dur, in: 0, out: +dur.toFixed(2) });
    keepSeq();
  }
  async function presetOptions(){
    if (!PRESETS) { try { PRESETS = await api("/api/presets"); } catch { PRESETS = { presets: {}, aspects: {} }; } }
    return PRESETS;
  }
  async function renderMaker(){
    const m = $("maker"); if (!m) return;
    $("mk-n").textContent = SEQ.length ? String(SEQ.length) : "";
    const btn = document.querySelector('#favview [data-fa="maker"]'); if (btn) btn.setAttribute("aria-pressed", String(MAKER));
    $("fav-main").classList.toggle("with-maker", MAKER);
    m.hidden = !MAKER;
    if (!MAKER) return;
    const P = await presetOptions();
    let opts = {};
    try { opts = JSON.parse(localStorage.getItem("cliptool-mkopts") || "{}"); } catch {}
    const o = { name: "", aspect: "vertical", fit: "blur", style: "highlight", captions: true, ...opts };
    const total = seqLen();
    m.innerHTML = `
      <div class="mk-head"><h3>🎬 Make a video</h3><span class="favnote">${SEQ.length} clip${SEQ.length === 1 ? "" : "s"} · ${secs(total)}</span></div>
      <div class="mk-player"><video id="mk-video" playsinline preload="metadata"></video>
        <div class="mk-pbar"><button type="button" data-fa="mkplay" ${SEQ.length ? "" : "disabled"}>${MK.playing ? "❚❚ Pause" : "▶ Play all"}</button>
        <span id="mk-now" class="favnote"></span></div></div>
      ${SEQ.length ? `<ol class="mk-seq">${SEQ.map((s, i) => `
        <li class="mk-item${MK.playing && MK.i === i ? " now" : ""}" draggable="true" data-seqi="${i}">
          <span class="mk-grip" title="Drag to reorder" aria-hidden="true">⋮⋮</span>
          ${s.thumb ? `<img src="${fileUrl(s.fav, s.thumb)}" alt="">` : `<span class="mk-noimg">🎞</span>`}
          <div class="mk-info">
            <b title="${esc(s.title)}">${i + 1}. ${esc(s.title)}</b>
            <div class="mk-trim">
              <label>In <input type="number" step="0.1" min="0" max="${s.dur}" value="${s.in.toFixed(1)}" data-seqin="${i}" aria-label="Start of clip ${i + 1} (seconds)"></label>
              <label>Out <input type="number" step="0.1" min="0" max="${s.dur}" value="${s.out.toFixed(1)}" data-seqout="${i}" aria-label="End of clip ${i + 1} (seconds)"></label>
              <span class="favnote">${secs(Math.max(0, s.out - s.in))} of ${secs(s.dur)}</span>
            </div>
          </div>
          <div class="mk-acts">
            <button type="button" data-fa="mkone" data-i="${i}" title="Play this clip (with its trim)" aria-label="Play clip ${i + 1}">▶</button>
            <button type="button" data-fa="mkup" data-i="${i}" ${i ? "" : "disabled"} aria-label="Move up">↑</button>
            <button type="button" data-fa="mkdown" data-i="${i}" ${i < SEQ.length - 1 ? "" : "disabled"} aria-label="Move down">↓</button>
            <button type="button" data-fa="mkdup" data-i="${i}" title="Use this clip twice" aria-label="Duplicate">⧉</button>
            <button type="button" class="danger" data-fa="mkdel" data-i="${i}" aria-label="Remove from video">✕</button>
          </div>
        </li>`).join("")}</ol>
        <div class="btnrow"><button type="button" class="danger" data-fa="mkclear">Clear all</button></div>`
        : `<p class="favnote mk-empty">Press <b>+ Add to video</b> on saved clips (from any folder). Drag them into order, trim each one,
            then create the video - it opens in the full editor for captions, logos, text, filters and more.</p>`}
      <div class="mk-opts">
        <label class="fld">Name<input id="mk-name" maxlength="80" value="${esc(o.name)}" placeholder="e.g. GTA 6 best moments"></label>
        <div class="mk-row">
          <label class="fld">Format<select id="mk-aspect">${Object.entries(P.aspects || {}).map(([k, a]) => `<option value="${k}" ${k === o.aspect ? "selected" : ""}>${esc(a.label)}</option>`).join("")}</select></label>
          <label class="fld">Layout<select id="mk-fit">${[["blur", "Full + blur"], ["black", "Full + black"], ["auto", "Auto (follow action)"], ["crop", "Crop to fill"], ["stack", "Facecam + game"]]
            .map(([k, l]) => `<option value="${k}" ${k === o.fit ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        </div>
        <label class="fld">Captions<select id="mk-style"><option value="" ${o.captions ? "" : "selected"}>No captions</option>${Object.entries(P.presets || {}).map(([k, p]) => `<option value="${k}" ${o.captions && k === o.style ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
        <button type="button" class="primary mk-go" data-fa="mkgo" ${SEQ.length ? "" : "disabled"}>🎬 Create video (${secs(total)})</button>
        <p class="favnote">Then it opens in the editor: change layout, captions, logos, text, filters, speed, cut parts out - and Save &amp; render.</p>
      </div>`;
    const name = $("mk-name");
    name.addEventListener("input", saveOpts);
  }
  function saveOpts(){
    const st = $("mk-style").value;
    const o = { name: $("mk-name").value, aspect: $("mk-aspect").value, fit: $("mk-fit").value, style: st || "highlight", captions: !!st };
    try { localStorage.setItem("cliptool-mkopts", JSON.stringify(o)); } catch {}
    return o;
  }
  /* Preview: play the pieces one after another, each from its In to its Out. */
  function playPiece(i, all){
    const v = $("mk-video"), s = SEQ[i]; if (!v || !s) { MK.playing = false; renderMaker(); return; }
    MK.i = i; MK.playing = true; MK.all = all;
    document.querySelectorAll("#maker .mk-item").forEach(n => n.classList.toggle("now", +n.dataset.seqi === i));
    const b = document.querySelector('#maker [data-fa="mkplay"]'); if (b) b.textContent = "❚❚ Pause";
    $("mk-now").textContent = `${i + 1}/${SEQ.length} · ${s.title}`;
    const go = () => { v.currentTime = s.in; v.play().catch(() => {}); };
    const src = fileUrl(s.fav, s.file);
    if (v.getAttribute("src") !== src) { v.src = src; v.addEventListener("loadedmetadata", go, { once: true }); } else go();
    v.ontimeupdate = () => {
      if (!MK.playing || MK.i !== i) return;
      if (v.currentTime >= s.out - 0.04) {
        if (MK.all && i + 1 < SEQ.length) playPiece(i + 1, true);
        else { v.pause(); MK.playing = false; const bb = document.querySelector('#maker [data-fa="mkplay"]'); if (bb) bb.textContent = "▶ Play all"; }
      }
    };
  }
  function onChange(e){
    const t = e.target;
    if (t.matches("[data-seqin], [data-seqout]")) {
      const i = +(t.dataset.seqin ?? t.dataset.seqout), s = SEQ[i]; if (!s) return;
      let v = Math.max(0, Math.min(s.dur, +t.value || 0));
      if (t.dataset.seqin !== undefined) s.in = Math.min(v, s.out - 0.3); else s.out = Math.max(v, s.in + 0.3);
      s.in = +s.in.toFixed(2); s.out = +s.out.toFixed(2);
      keepSeq(); renderMaker();
    }
    if (t.matches("#mk-aspect, #mk-fit, #mk-style")) saveOpts();
  }
  async function onClick(e){
    const b = e.target.closest("[data-fa]"); if (!b) return;
    const act = b.dataset.fa, i = +b.dataset.i;
    const item = () => ITEMS[+b.dataset.n];
    try {
      if (act === "close") close();
      else if (act === "folder") showFolder(b.dataset.name);
      else if (act === "maker") { MAKER = !MAKER; renderMaker(); }
      else if (act === "add") {
        addToSeq(item(), CUR); MAKER = true; renderMaker(); showFolder(CUR);
        toast(`Added “${item().title}” (${SEQ.length} in your video)`);
      }
      else if (act === "addall") { ITEMS.slice().reverse().forEach(it => addToSeq(it, CUR)); MAKER = true; renderMaker(); showFolder(CUR); }
      else if (act === "mkplay") { if (MK.playing) { $("mk-video").pause(); MK.playing = false; renderMaker(); } else playPiece(0, true); }
      else if (act === "mkone") playPiece(i, false);
      else if (act === "mkup" && i > 0) { [SEQ[i - 1], SEQ[i]] = [SEQ[i], SEQ[i - 1]]; keepSeq(); renderMaker(); }
      else if (act === "mkdown" && i < SEQ.length - 1) { [SEQ[i + 1], SEQ[i]] = [SEQ[i], SEQ[i + 1]]; keepSeq(); renderMaker(); }
      else if (act === "mkdup") { SEQ.splice(i + 1, 0, { ...SEQ[i] }); keepSeq(); renderMaker(); }
      else if (act === "mkdel") { SEQ.splice(i, 1); keepSeq(); renderMaker(); if (CUR) showFolder(CUR); }
      else if (act === "mkclear") { if (confirm("Remove all clips from the video you're making?")) { SEQ = []; keepSeq(); renderMaker(); if (CUR) showFolder(CUR); } }
      else if (act === "mkgo") {
        const o = saveOpts();
        if (!SEQ.length) return;
        b.disabled = true;
        stopVideos();
        const d = await post("/api/favs/make-video", { name: o.name || "My video", aspect: o.aspect, fit: o.fit, style: o.style, captions: o.captions,
          pieces: SEQ.map(s => ({ fav: s.fav, key: s.key, in: s.in, out: s.out })) });
        toast("Making your video - it opens in the editor when it's ready");
        poll(d.id);
        renderMaker();
      }
      else if (act === "new") {
        const n = $("fav-newname").value.trim(); if (!n) { $("fav-newname").focus(); return; }
        const d = await post("/api/favs", { name: n }); $("fav-newname").value = "";
        await folders(); showFolder(d.name); toast(`Folder “${d.name}” created`);
      }
      else if (act === "openroot") post("/api/open", { which: "favorites" });
      else if (act === "openf") post("/api/open", { which: "fav", name: CUR });
      else if (act === "rename") {
        const n = prompt("New name for this folder:", CUR); if (!n || n.trim() === CUR) return;
        stopVideos();
        const d = await post(`/api/favs/${enc(CUR)}/rename`, { to: n });
        SEQ.forEach(s => { if (s.fav === CUR) s.fav = d.name; }); keepSeq();
        await folders(); showFolder(d.name); renderMaker();
      }
      else if (act === "delf") {
        const f = FOLDERS.find(x => x.name === CUR);
        if (!confirm(`Delete the folder “${CUR}” and the ${f ? f.count : ""} clips saved in it?\n\nYour runs are not touched. This can't be undone.`)) return;
        stopVideos();
        await api(`/api/favs/${enc(CUR)}`, { method: "DELETE" }); toast(`Folder “${CUR}” deleted`);
        SEQ = SEQ.filter(s => s.fav !== CUR); keepSeq();
        await folders(); renderMaker(); FOLDERS.length ? showFolder(FOLDERS[0].name) : showEmpty();
      }
      else if (act === "remove") {
        const it = item(); if (!confirm(`Remove “${it ? it.title : "this clip"}” from “${CUR}”?`)) return;
        stopVideos();
        await api(`/api/favs/${enc(CUR)}/items/${enc(it.key)}`, { method: "DELETE" });
        SEQ = SEQ.filter(s => !(s.fav === CUR && s.key === it.key)); keepSeq();
        await folders(); showFolder(CUR); renderMaker();
      }
      else if (act === "ctitle") { const it = item(); if (it) copy(it.title); }
    } catch (err) { toast(err.message); if (act === "mkgo") renderMaker(); }
  }

  window.Fav = { pick, saveRange, open, folders: () => FOLDERS, refresh: folders, onJobDone };
  folders();
})();
