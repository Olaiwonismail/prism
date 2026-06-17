/* Prism site. One script shared by all pages, no build step; each page only
 * runs the parts whose elements exist:
 *   home      — hero canvas, smart "get latest" button, roadmap teaser
 *   roadmap/  — phase list + detail panel rendered from roadmap.md
 *   releases/ — releases from the GitHub API, paired by version with the
 *               markdown build notes in docs/devlog/
 *
 * Pages set <body data-root> ("" at docs/, "../" one level down) so shared
 * fetches resolve from any depth.
 */

const OWNER = "Olaiwonismail";
const REPO = "prism";
const REPO_URL = `https://github.com/${OWNER}/${REPO}`;
const RAW = `https://raw.githubusercontent.com/${OWNER}/${REPO}/main/`;
const ROOT = document.body.dataset.root || "";

// Deployed FastAPI cleaner (Cloud Run). The "try it" section POSTs WAVs here;
// swap for http://localhost:8000 when testing against a local uvicorn.
const API = "https://prism-830276903442.europe-west1.run.app";

/* ---- tiny markdown renderer (for content we write ourselves) ---------- */

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/—/g, "&mdash;");
}

function md(src) {
  const out = [];
  let para = [], inCode = false, inList = false;
  const flushPara = () => {
    if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
  };
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };

  for (const raw of esc(src).split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.startsWith("```")) {
      flushPara(); closeList();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) { out.push(line); continue; }

    const h = line.match(/^(#{1,3}) +(.*)/);
    if (h) {
      flushPara(); closeList();
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      continue;
    }
    const li = line.match(/^[-*] +(.*)/);
    if (li) {
      flushPara();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (line.trim() === "") { flushPara(); closeList(); continue; }
    para.push(line.trim());
  }
  flushPara(); closeList();
  return out.join("\n");
}

/* Turn "Status: done" lines into badges. */
function badge(html) {
  return html.replace(/Status: *(done|next|in progress|planned)/gi, (m, s) => {
    const cls = /done/i.test(s) ? "done" : /next|progress/i.test(s) ? "next" : "";
    return `<span class="badge ${cls}">${s}</span>`;
  });
}

async function fetchFirst(urls) {
  for (const u of urls) {
    try {
      const r = await fetch(u);
      if (r.ok) return await r.text();
    } catch (e) { /* try the next one */ }
  }
  return null;
}

/* ---- roadmap: phase list on the left, detail panel on the right --------- */

function parseRoadmap(text) {
  // Drop the file's own H1 + intro; split the rest into ## phase sections.
  const body = text.replace(/^# .*\n+([^\n#][^\n]*\n+)*/, "");
  const phases = [];
  for (const chunk of body.split(/^## +/m).slice(1)) {
    const nl = chunk.indexOf("\n");
    const title = chunk.slice(0, nl).trim();
    const rest = chunk.slice(nl + 1);
    const sm = rest.match(/Status: *([a-z ]+)/i);
    const tm = title.match(/^(Phase \d+)\s*[—–-]+\s*(.*)$/i);
    phases.push({
      num: tm ? tm[1] : title,
      name: tm ? tm[2] : "",
      status: sm ? sm[1].trim().toLowerCase() : "planned",
      detail: rest.replace(/Status:[^\n]*\n?/i, "").trim(),
    });
  }
  return phases;
}

function statusCls(s) {
  return /done/.test(s) ? "done" : /next|progress/.test(s) ? "next" : "planned";
}

/* roadmap.md lives at the repo root: one level above docs/ locally, which is
 * above the web root on GitHub Pages — hence the raw.githubusercontent fallback. */
function fetchRoadmap() {
  return fetchFirst([ROOT + "../roadmap.md", RAW + "roadmap.md"]);
}

async function loadRoadmap() {
  const el = document.getElementById("roadmap-body");
  const text = await fetchRoadmap();
  if (!text) {
    el.innerHTML = `<p class="muted">Couldn't load the roadmap here — ` +
      `<a href="${REPO_URL}/blob/main/roadmap.md" target="_blank" rel="noopener">read it on GitHub</a>.</p>`;
    return;
  }
  const phases = parseRoadmap(text);
  if (phases.length < 2) { // structure changed: fall back to a plain render
    el.innerHTML = badge(md(text.replace(/^# .*\n/, "")));
    return;
  }

  el.innerHTML = `<div class="roadmap-grid">
    <div class="phase-list">${phases.map((p, i) => `
      <button class="phase-item ${statusCls(p.status)}" data-i="${i}">
        <span class="phase-ico" aria-hidden="true"></span>
        <span class="phase-label">
          <span class="mono phase-num">${esc(p.num.toLowerCase())}</span>
          ${esc(p.name)}
        </span>
        <span class="phase-go" aria-hidden="true">&#9656;</span>
      </button>`).join("")}
    </div>
    <div class="phase-panel"></div>
  </div>`;

  const items = el.querySelectorAll(".phase-item");
  const panel = el.querySelector(".phase-panel");

  function select(i) {
    items.forEach((b, j) => {
      b.classList.toggle("active", i === j);
      if (i === j) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
    const p = phases[i];
    panel.innerHTML = `<div class="phase-head">
        <h3>${esc(p.name || p.num)}</h3>
        <span class="badge ${statusCls(p.status)}">${esc(p.status)}</span>
      </div>
      <div class="md">${md(p.detail)}</div>`;
  }

  items.forEach((b) => b.addEventListener("click", () => select(+b.dataset.i)));
  // Land on the phase that's up next — that's where the project lives now.
  const next = phases.findIndex((p) => statusCls(p.status) === "next");
  select(next >= 0 ? next : 0);
}

/* ---- releases & devlog -------------------------------------------------- */

async function loadDevlogs() {
  try {
    const r = await fetch(ROOT + "devlog.json");
    if (!r.ok) return new Map();
    const { entries } = await r.json();
    const logs = new Map();
    for (const v of entries) {
      const text = await fetchFirst([ROOT + `devlog/${v}.md`, RAW + `docs/devlog/${v}.md`]);
      if (text) logs.set(v, text);
    }
    return logs;
  } catch (e) { return new Map(); }
}

function devlogDetails(version, text, open) {
  return `<details class="devlog"${open ? " open" : ""}>
    <summary>devlog · build notes for ${version}</summary>
    <div class="md">${md(text.replace(/^# .*\n/, ""))}</div>
  </details>`;
}

function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US",
    { year: "numeric", month: "short", day: "numeric" });
}

function fmtSize(bytes) {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
}

/* Master-detail, same bones as the roadmap: versions down the left rail,
 * the selected release (changelog, downloads, build notes) in the panel.
 * Unreleased devlog entries sit on top of the rail marked "in progress";
 * the newest real release is selected by default. */
async function loadReleases() {
  const el = document.getElementById("releases-body");
  const logs = await loadDevlogs();

  let releases = [];
  try {
    const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases`);
    if (r.ok) releases = await r.json();
  } catch (e) { /* offline or rate-limited: fall through to empty state */ }

  const released = new Set(releases.map(r => r.tag_name));
  const entries = [];
  for (const [version, log] of logs) {
    if (!released.has(version)) entries.push({ kind: "wip", version, log });
  }
  for (const rel of releases) {
    entries.push({ kind: "release", rel, log: logs.get(rel.tag_name) || null });
  }

  const buildbox = releases.length ? ""
    : document.getElementById("no-releases").innerHTML;
  if (!entries.length) { el.innerHTML = buildbox; return; }

  el.innerHTML = `${buildbox}
  <div class="roadmap-grid">
    <div class="phase-list">${entries.map((e, i) => {
      const wip = e.kind === "wip";
      return `<button class="phase-item ${wip ? "planned" : "done"}" data-i="${i}">
        <span class="phase-ico" aria-hidden="true"></span>
        <span class="phase-label">
          <span class="mono phase-num">${esc(wip ? "in progress" : fmtDate(e.rel.published_at))}</span>
          ${esc(wip ? e.version : e.rel.tag_name || e.rel.name)}
        </span>
        <span class="phase-go" aria-hidden="true">&#9656;</span>
      </button>`;
    }).join("")}
    </div>
    <div class="phase-panel"></div>
  </div>`;

  const items = el.querySelectorAll(".phase-item");
  const panel = el.querySelector(".phase-panel");
  const latest = entries.findIndex(e => e.kind === "release");

  function show(i) {
    items.forEach((b, j) => {
      b.classList.toggle("active", i === j);
      if (i === j) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
    const e = entries[i];
    if (e.kind === "wip") {
      // No release to pair with yet: the build notes are the whole story.
      panel.innerHTML = `<div class="phase-head">
          <h3>${esc(e.version)}</h3>
          <span class="chip next mono">in progress · unreleased</span>
        </div>
        <div class="md">${md(e.log.replace(/^# .*\n/, ""))}</div>`;
      return;
    }
    const rel = e.rel;
    const assets = (rel.assets || []).map(a =>
      `<a class="asset" href="${a.browser_download_url}">${esc(a.name)} · ${fmtSize(a.size)}</a>`
    ).join("");
    panel.innerHTML = `<div class="phase-head">
        <h3>${esc(rel.name || rel.tag_name)}</h3>
        <span class="release-date mono">${i === latest ? "latest · " : ""}${esc(rel.tag_name)} · ${fmtDate(rel.published_at)}</span>
      </div>
      <div class="md">${rel.body ? md(rel.body) : '<p class="muted">No notes for this release.</p>'}</div>
      ${assets ? `<div class="assets">${assets}</div>` : ""}
      ${e.log ? devlogDetails(rel.tag_name, e.log, false) : ""}`;
  }

  items.forEach(b => b.addEventListener("click", () => show(+b.dataset.i)));
  show(latest >= 0 ? latest : 0);
}

/* ---- hero canvas: noise in, signal out ---------------------------------- */

function heroWave() {
  const canvas = document.getElementById("wave");
  const toggle = document.getElementById("filter-toggle");
  const ctx = canvas.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Per-theme canvas palette; the light set is darkened to hold contrast
  // against paper the way the dark set glows against ink.
  const PAL = {
    dark: {
      raw: "rgba(150,160,172,.5)",
      prism: "rgba(232,234,238,.5)",
      prismGlow: "rgba(111,201,216,.22)",
      waveGlow: "rgba(155,144,212,.4)",
      speck: "150,160,172",
      stops: ["#6fc9d8", "#9b90d4", "#d3a3d9"],
    },
    light: {
      raw: "rgba(90,100,112,.55)",
      prism: "rgba(40,46,54,.45)",
      prismGlow: "rgba(44,122,135,.16)",
      waveGlow: "rgba(91,102,160,.28)",
      speck: "90,100,112",
      stops: ["#2f97ab", "#6f68bd", "#a87fae"],
    },
  };
  const pal = () =>
    PAL[document.documentElement.dataset.theme === "light" ? "light" : "dark"];

  let filtered = true;
  let W = 0, H = 0, t = 0;
  const specks = [];

  function resize() {
    const dpr = devicePixelRatio || 1;
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  const wave = (x, t) =>
    0.34 * Math.sin(x * 0.020 - t * 1.1) +
    0.18 * Math.sin(x * 0.043 - t * 1.9);
  const noise = (x, t) =>
    0.15 * Math.sin(x * 0.131 + t * 7.3) +
    0.11 * Math.sin(x * 0.071 - t * 9.1) +
    0.07 * Math.sin(x * 0.233 + t * 5.7) +
    0.05 * Math.sin(x * 0.389 - t * 11.3);

  function trace(x0, x1, fn, stroke, width, glow) {
    ctx.beginPath();
    for (let x = x0; x <= x1; x += 2) {
      const y = H / 2 + fn(x) * H * 0.36;
      x === x0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.shadowBlur = glow || 0;
    ctx.shadowColor = glow ? pal().waveGlow : "transparent";
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    const P = pal();
    const cx = W / 2, side = Math.min(74, H * 0.34);
    const pL = cx - side * 0.62, pR = cx + side * 0.62;

    // Incoming: signal + noise, in gray.
    trace(0, pL, x => wave(x, t) + noise(x, t), P.raw, 1.6);

    // The prism.
    ctx.beginPath();
    ctx.moveTo(cx, H / 2 - side * 0.72);
    ctx.lineTo(cx + side * 0.62, H / 2 + side * 0.55);
    ctx.lineTo(cx - side * 0.62, H / 2 + side * 0.55);
    ctx.closePath();
    ctx.strokeStyle = P.prism;
    ctx.lineWidth = 1.4;
    ctx.shadowBlur = 12;
    ctx.shadowColor = P.prismGlow;
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Outgoing: clean spectrum-lit signal, or untouched noise when off.
    if (filtered) {
      const grad = ctx.createLinearGradient(pR, 0, W, 0);
      grad.addColorStop(0, P.stops[0]);
      grad.addColorStop(0.55, P.stops[1]);
      grad.addColorStop(1, P.stops[2]);
      trace(pR, W, x => wave(x, t), grad, 2, 10);
    } else {
      trace(pR, W, x => wave(x, t) + noise(x, t), P.raw, 1.6);
    }

    // The removed noise falls away beneath the prism as fading specks.
    if (filtered && !reduced && Math.random() < 0.30 && specks.length < 36) {
      specks.push({
        x: cx + (Math.random() - 0.5) * side * 0.7,
        y: H / 2 + side * 0.45,
        vx: 0.25 + Math.random() * 0.5,
        vy: 0.35 + Math.random() * 0.55,
        a: 0.5,
      });
    }
    for (let i = specks.length - 1; i >= 0; i--) {
      const s = specks[i];
      s.x += s.vx; s.y += s.vy; s.a -= 0.006;
      if (s.a <= 0 || s.y > H) { specks.splice(i, 1); continue; }
      ctx.fillStyle = `rgba(${pal().speck},${s.a})`;
      ctx.fillRect(s.x, s.y, 2, 2);
    }
  }

  function frame() {
    t += 0.014;
    draw();
    requestAnimationFrame(frame);
  }

  toggle.addEventListener("click", () => {
    filtered = !filtered;
    toggle.textContent = filtered ? "filtering · ON" : "filtering · OFF";
    toggle.setAttribute("aria-pressed", String(filtered));
    if (reduced) draw();
  });

  addEventListener("resize", () => { resize(); if (reduced) draw(); });
  // The animation loop picks theme changes up next frame; the static
  // reduced-motion canvas needs an explicit repaint.
  addEventListener("themechange", () => { if (reduced) draw(); });
  resize();
  if (reduced) { t = 4; draw(); } else { frame(); }
}

/* ---- light/dark toggle ---------------------------------------------------- */

function initTheme() {
  const btn = document.getElementById("theme-toggle");
  const root = document.documentElement;
  const paint = () => {
    const light = root.dataset.theme === "light";
    btn.textContent = light ? "☾" : "☀";
    const label = light ? "Switch to dark mode" : "Switch to light mode";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  };
  btn.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
    paint();
    dispatchEvent(new Event("themechange"));
  });
  paint();
}

/* ---- home page extras ---------------------------------------------------- */

/* "Get the latest version": once a release exists, the button names it and
 * links straight to the download (single asset) or the release page. */
async function loadLatestButton() {
  const btn = document.getElementById("get-latest");
  try {
    // /releases/latest skips prereleases; the list endpoint includes them, so
    // take the newest non-draft so early prerelease builds still surface here.
    const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases`);
    if (!r.ok) return; // no releases yet: keep pointing at the releases page
    const rel = (await r.json()).find(x => !x.draft);
    if (!rel) return;
    btn.textContent = `Get Prism ${rel.tag_name}`;
    btn.href = (rel.assets || []).length === 1
      ? rel.assets[0].browser_download_url
      : "releases/";
  } catch (e) { /* offline: the default link is already right */ }
}

/* One line of momentum above the bottom CTA, read from the roadmap. */
async function loadTeaser() {
  const el = document.getElementById("roadmap-teaser");
  const text = await fetchRoadmap();
  if (!text) return; // the static fallback line is already in the HTML
  const phases = parseRoadmap(text);
  const done = phases.filter(p => statusCls(p.status) === "done");
  const next = phases.find(p => statusCls(p.status) === "next");
  if (!done.length || !next) return;
  el.innerHTML = `${done.length} of ${phases.length} phases shipped. ` +
    `Up next: ${esc(next.name.toLowerCase() || next.num)}. ` +
    `<a href="roadmap/">See the roadmap →</a>`;
}

/* ---- "try it": record -> clean via API -> play/download ----------------- */

/* Encode mono Float32 PCM as a 16-bit WAV blob. MediaRecorder gives us
 * webm/opus, which the server's libsndfile can't read, so we decode to PCM in
 * the browser and re-wrap it as a WAV the API understands. */
function encodeWav(samples, sampleRate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  str(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  view.setUint32(16, 16, true);     // PCM chunk size
  view.setUint16(20, 1, true);      // format = PCM
  view.setUint16(22, 1, true);      // channels = mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);      // block align
  view.setUint16(34, 16, true);     // bits per sample
  str(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

/* Decode a recorded blob to a mono Float32 WAV the API can read. */
async function blobToWav(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
  const n = audio.length;
  const mono = new Float32Array(n);
  for (let ch = 0; ch < audio.numberOfChannels; ch++) {
    const data = audio.getChannelData(ch);
    for (let i = 0; i < n; i++) mono[i] += data[i] / audio.numberOfChannels;
  }
  const wav = encodeWav(mono, audio.sampleRate);
  ctx.close();
  return wav;
}

function initTryIt() {
  const card = document.querySelector(".try-card");
  const recordBtn = document.getElementById("try-record");
  const recordLabel = document.getElementById("try-record-label");
  const timerEl = document.getElementById("try-timer");
  const cleanBtn = document.getElementById("try-clean");
  const modelSel = document.getElementById("try-model");
  const status = document.getElementById("try-status");
  const rawTake = document.getElementById("try-take-raw");
  const outTake = document.getElementById("try-take-out");
  const rawAudio = document.getElementById("try-raw");
  const outAudio = document.getElementById("try-out");
  const download = document.getElementById("try-download");

  let recorder = null, chunks = [], take = null, recording = false;
  let ticker = null, startedAt = 0;
  const say = (msg) => { status.textContent = msg; };
  const setState = (s) => { card.dataset.state = s; };

  const fmt = (sec) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  function tick() {
    timerEl.textContent = fmt(Math.floor((Date.now() - startedAt) / 1000));
  }

  async function start() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      say("Mic access was blocked. Allow the microphone and try again.");
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop()); // release the mic
      clearInterval(ticker);
      take = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      rawAudio.src = URL.createObjectURL(take);
      rawTake.hidden = false;
      cleanBtn.disabled = false;
      setState("recorded");
      recordLabel.textContent = "Record again";
      say("Got your recording. Pick a model and press Remove noise.");
    };
    recorder.start();
    recording = true;
    startedAt = Date.now();
    timerEl.textContent = "0:00";
    ticker = setInterval(tick, 250);
    setState("recording");
    recordLabel.textContent = "Recording… tap to stop";
    say("Listening — speak, then tap to stop.");
  }

  function stop() {
    if (recorder && recording) recorder.stop();
    recording = false;
  }

  recordBtn.addEventListener("click", () => (recording ? stop() : start()));

  cleanBtn.addEventListener("click", async () => {
    if (!take) return;
    cleanBtn.disabled = true;
    setState("cleaning");
    say("Cleaning… uploading to the Prism cleaner.");
    try {
      const wav = await blobToWav(take);
      const form = new FormData();
      form.append("file", wav, "take.wav");
      const r = await fetch(`${API}/clean?denoiser=${encodeURIComponent(modelSel.value)}`,
        { method: "POST", body: form });
      if (!r.ok) throw new Error(`server returned ${r.status}`);
      const cleaned = await r.blob();
      const url = URL.createObjectURL(cleaned);
      outAudio.src = url;
      download.href = url;
      download.hidden = false;
      outTake.hidden = false;
      setState("recorded");
      say("Done. Play the cleaned version or download it.");
    } catch (e) {
      setState("recorded");
      say(`Couldn't clean that clip (${e.message}). Try again in a moment.`);
    } finally {
      cleanBtn.disabled = false;
    }
  });
}

/* ---- per-page init: run only what exists on this page -------------------- */

if (document.getElementById("theme-toggle")) initTheme();
if (document.getElementById("wave")) heroWave();
if (document.getElementById("try")) initTryIt();
if (document.getElementById("get-latest")) loadLatestButton();
if (document.getElementById("roadmap-teaser")) loadTeaser();
if (document.getElementById("roadmap-body")) loadRoadmap();
if (document.getElementById("releases-body")) loadReleases();
