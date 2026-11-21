/* ClipTool favorites: save clips into your own folders, and browse what you saved.
 *
 * Uses globals from index.html: $, esc, fmt, toast, copyText (optional), releaseVideos.
 * Exposes window.Fav = { pick, save, open, folders }.
 */
(() => {
  let FOLDERS = [];
  let CUR = null;          // folder shown in the viewer
  const api = (u, o) => fetch(u, o).then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || "Something went wrong"); return d; });
  const post = (u, body) => api(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const enc = encodeURIComponent;
  const copy = async t => { try { await navigator.clipboard.writeText(t); toast("Copied"); } catch { toast("Couldn't copy"); } };

  async function folders(){
    try { FOLDERS = (await api("/api/favs")).folders || []; } catch { FOLDERS = []; }
    const n = FOLDERS.reduce((a, f) => a + f.count, 0);
    const b = $("open-favs"); if (b) b.querySelector(".favn").textContent = n ? String(n) : "";
    return FOLDERS;
  }

  /* Copy clips of a run into a folder. */
  async function save(runFolder, ranks, name, create){
    const d = await post(`/api/favs/${enc(name)}/add`, { folder: runFolder, ranks, create: !!create });
    const msg = d.added.length ? `Saved ${d.added.length} clip${d.added.length > 1 ? "s" : ""} to “${d.name}”` : "Nothing saved";
    toast(msg + (d.replaced ? ` (${d.replaced} updated)` : "") + (d.skipped.length ? ` - ${d.skipped.length} not rendered yet` : ""));
    folders();
    if (CUR === d.name && !$("favview").hidden) showFolder(d.name);
    return d;
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
  /* Ask which folder; resolves with {name, create} or null. `opts.now` saves right away (runFolder + ranks). */
  function pick(anchor, opts = {}){
    return new Promise(async resolve => {
      const p = pop();
      await folders();
      const count = opts.ranks ? opts.ranks.length : 1;
      p.innerHTML = `
        <h3>${esc(opts.label || `Save ${count} clip${count > 1 ? "s" : ""} to…`)}</h3>
        ${FOLDERS.length ? `<div class="favlist">${FOLDERS.map((f, i) => `<button type="button" data-i="${i}"><span>📁 ${esc(f.name)}</span><small>${f.count}</small></button>`).join("")}</div>`
          : `<p class="favnote">No folders yet - make your first one:</p>`}
        <div class="favnew"><input id="favnew-name" maxlength="60" placeholder="New folder, e.g. Best GTA moments" aria-label="New folder name">
          <button type="button" class="primary" data-new>Create${opts.now ? " & save" : ""}</button></div>`;
      const r = anchor.getBoundingClientRect();
      p.hidden = false;
      const w = p.offsetWidth, h = p.offsetHeight;
      p.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
      p.style.top = (r.bottom + h + 8 < window.innerHeight ? r.bottom + 6 : Math.max(8, r.top - h - 6)) + "px";
      const done = async (name, create) => {
        p.hidden = true;
        if (opts.now) { try { await save(opts.now.folder, opts.now.ranks, name, create); } catch (e) { toast(e.message); } }
        resolve({ name, create });
      };
      p.onclick = e => {
        const b = e.target.closest("[data-i]");
        if (b) return done(FOLDERS[+b.dataset.i].name, false);
        if (e.target.closest("[data-new]")) {
          const n = $("favnew-name").value.trim();
          if (!n) { $("favnew-name").focus(); return; }
          done(n, true);
        }
      };
      $("favnew-name").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); p.querySelector("[data-new]").click(); } };
      (FOLDERS.length ? p.querySelector("[data-i]") : $("favnew-name")).focus();
    });
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
        <button type="button" data-fa="openroot" title="Open the Favorites folder in Explorer">Open in Explorer</button>
      </header>
      <div class="fav-main">
        <aside class="fav-side">
          <div class="favnew"><input id="fav-newname" maxlength="60" placeholder="New folder name" aria-label="New folder name">
            <button type="button" data-fa="new">+ New</button></div>
          <nav class="fav-folders" id="fav-folders" aria-label="Your folders"></nav>
        </aside>
        <section class="fav-content" id="fav-content" aria-live="polite"></section>
      </div>`;
    document.body.appendChild(v);
    v.addEventListener("click", onClick);
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !v.hidden && $("favpop")?.hidden !== false) close(); });
    $("fav-newname").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); v.querySelector('[data-fa="new"]').click(); } });
    return v;
  }
  function stopVideos(){
    const v = $("favview"); if (!v) return;
    v.querySelectorAll("video").forEach(x => { x.pause(); x.removeAttribute("src"); x.load(); });
  }
  async function open(name){
    if (typeof releaseVideos === "function") releaseVideos();
    const v = view(); v.hidden = false; document.body.style.overflow = "hidden";
    await folders();
    try { $("fav-root").textContent = (await api("/api/favs")).root; } catch {}
    renderFolders();
    const pickName = name || CUR || (FOLDERS[0] && FOLDERS[0].name);
    if (pickName && FOLDERS.some(f => f.name === pickName)) showFolder(pickName); else showEmpty();
  }
  function close(){ stopVideos(); $("favview").hidden = true; document.body.style.overflow = ""; }
  function renderFolders(){
    $("fav-folders").innerHTML = FOLDERS.length ? FOLDERS.map(f => `
      <button type="button" class="fav-f${f.name === CUR ? " sel" : ""}" data-fa="folder" data-name="${esc(f.name)}" aria-current="${f.name === CUR}">
        ${f.cover ? `<img src="/favfile/${f.cover.split("/").map(enc).join("/")}" alt="">` : `<span class="fav-ico">📁</span>`}
        <span class="fav-fn"><b>${esc(f.name)}</b><small>${f.count} clip${f.count === 1 ? "" : "s"} · ${f.size_mb} MB</small></span>
      </button>`).join("") : `<p class="favnote">No folders yet. Create one above, or tick clips in a run and press <b>★ Save to folder</b>.</p>`;
  }
  function showEmpty(){
    CUR = null; renderFolders();
    $("fav-content").innerHTML = `<div class="fav-empty"><p>Pick a folder on the left, or create one.</p>
      <p class="favnote">Save clips from a run (tick them, then <b>★ Save to folder</b>) or from the <b>Full video</b> view.</p></div>`;
  }
  async function showFolder(name){
    stopVideos();
    CUR = name; renderFolders();
    let d;
    try { d = await api(`/api/favs/${enc(name)}`); } catch (e) { toast(e.message); await folders(); renderFolders(); return showEmpty(); }
    const f = FOLDERS.find(x => x.name === name) || { size_mb: 0 };
    const base = `/favfile/${enc(d.name)}/`;
    $("fav-content").innerHTML = `
      <div class="fav-head">
        <div><h3>📁 ${esc(d.name)}</h3><p class="favnote">${d.items.length} clip${d.items.length === 1 ? "" : "s"} · ${f.size_mb} MB · <span class="fav-path">${esc(d.path)}</span></p></div>
        <div class="fav-tools">
          <a class="btnlike" href="/api/favs/${enc(d.name)}/export.xlsx" download title="No., title, description of every clip in this folder">⬇ List (.xlsx)</a>
          <a class="btnlike" href="/api/favs/${enc(d.name)}/export.csv" download>CSV</a>
          <button type="button" data-fa="openf">Open in Explorer</button>
          <button type="button" data-fa="rename">Rename</button>
          <button type="button" class="danger" data-fa="delf">Delete folder</button>
        </div>
      </div>
      ${d.items.length ? `<div class="fav-grid">${d.items.map(i => `
        <article class="fav-card">
          <video controls preload="metadata" playsinline ${i.thumb ? `poster="${base + enc(i.thumb)}"` : ""} src="${base + enc(i.file)}" aria-label="${esc(i.title)}"></video>
          <div class="fav-body">
            <h4 title="${esc(i.title)}">${esc(i.title)}</h4>
            <p class="favnote" title="${esc(i.video_title || "")}">${esc(i.channel || "")} · ${i.source_url ? `<a href="${esc(i.source_url)}" target="_blank" rel="noopener">${fmt(i.start)}–${fmt(i.end)}</a>` : `${fmt(i.start)}–${fmt(i.end)}`} · ${i.size_mb} MB</p>
            <div class="fav-acts">
              ${i.editable ? `<button type="button" class="favedit" data-fa="edit" data-key="${esc(i.key)}" title="Edit this clip (trim, cut out parts, captions, logos...). Saving updates the copy in this folder.">✎ Edit</button>`
                : `<button type="button" disabled title="Its original run was deleted, so it can't be re-edited. You can still play and download it.">✎ Edit</button>`}
              <a href="${base + enc(i.file)}?download=1" title="Download MP4" aria-label="Download">⬇</a>
              <button type="button" data-fa="cdesc" data-key="${esc(i.key)}" title="Copy upload description">Description</button>
              <button type="button" data-fa="ctitle" data-key="${esc(i.key)}" title="Copy title">Title</button>
              <button type="button" class="danger" data-fa="remove" data-key="${esc(i.key)}" title="Remove from this folder" aria-label="Remove ${esc(i.title)}">✕</button>
            </div>
          </div>
        </article>`).join("")}</div>`
        : `<div class="fav-empty"><p>This folder is empty.</p><p class="favnote">Open a run, tick clips and press <b>★ Save to folder</b>.</p></div>`}`;
    $("fav-content").dataset.items = JSON.stringify(d.items.map(i => ({ key: i.key, title: i.title, description: i.description, run: i.run, rank: i.rank })));
  }
  async function onClick(e){
    const b = e.target.closest("[data-fa]"); if (!b) return;
    const act = b.dataset.fa;
    const item = () => JSON.parse($("fav-content").dataset.items || "[]").find(i => i.key === b.dataset.key);
    try {
      if (act === "close") close();
      else if (act === "folder") showFolder(b.dataset.name);
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
        const d = await post(`/api/favs/${enc(CUR)}/rename`, { to: n }); await folders(); showFolder(d.name);
      }
      else if (act === "delf") {
        const f = FOLDERS.find(x => x.name === CUR);
        if (!confirm(`Delete the folder “${CUR}” and the ${f ? f.count : ""} clip videos saved in it?\n\nThe original clips in your runs are not touched. This can't be undone.`)) return;
        stopVideos();
        await api(`/api/favs/${enc(CUR)}`, { method: "DELETE" }); toast(`Folder “${CUR}” deleted`);
        await folders(); FOLDERS.length ? showFolder(FOLDERS[0].name) : showEmpty();
      }
      else if (act === "remove") {
        const i = item(); if (!confirm(`Remove “${i ? i.title : "this clip"}” from “${CUR}”?\n\nOnly the saved copy is removed.`)) return;
        stopVideos();
        await api(`/api/favs/${enc(CUR)}/items/${enc(b.dataset.key)}`, { method: "DELETE" });
        await folders(); showFolder(CUR);
      }
      else if (act === "edit") {
        const i = item(); if (!i) return;
        const name = CUR; close();
        await openRun(i.run);
        if (!currentRun || !currentRun.clips.some(c => c.rank === i.rank)) { toast("That clip isn't in its run any more."); return; }
        await openEditor(i.run, i.rank, { fav: name });
        toast(`Editing “${i.title}” - Save & render also updates the copy in “${name}”`);
      }
      else if (act === "cdesc") { const i = item(); if (i) copy(i.description); }
      else if (act === "ctitle") { const i = item(); if (i) copy(i.title); }
    } catch (err) { toast(err.message); }
  }

  window.Fav = { pick, save, open, folders: () => FOLDERS, refresh: folders };
  folders();
})();
