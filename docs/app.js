/* Prism landing page.
 *
 * Three jobs, no build step:
 *   1. Hero canvas — a noisy waveform passes through a prism and comes out
 *      clean; the removed noise falls away as specks. Toggleable.
 *   2. Roadmap — fetched from roadmap.md (repo root locally, raw GitHub on
 *      Pages) and rendered with a tiny markdown converter.
 *   3. Releases & devlog — releases from the GitHub API, paired by version
 *      with the markdown stories in docs/devlog/.
 */

const OWNER = "Olaiwonismail";
const REPO = "prism";
const REPO_URL = `https://github.com/${OWNER}/${REPO}`;
const RAW = `https://raw.githubusercontent.com/${OWNER}/${REPO}/main/`;

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

/* ---- roadmap ----------------------------------------------------------- */

async function loadRoadmap() {
  const el = document.getElementById("roadmap-body");
  const text = await fetchFirst(["../roadmap.md", RAW + "roadmap.md"]);
  if (!text) {
    el.innerHTML = `<p class="muted">Couldn't load the roadmap here — ` +
      `<a href="${REPO_URL}/blob/main/roadmap.md" target="_blank" rel="noopener">read it on GitHub</a>.</p>`;
    return;
  }
  // Drop the file's own H1 + intro line; the section already has a heading.
  const body = text.replace(/^# .*\n+([^\n#][^\n]*\n+)*/, "");
  el.innerHTML = badge(md(body));
}

/* ---- releases & devlog -------------------------------------------------- */

async function loadDevlogs() {
  try {
    const r = await fetch("devlog.json");
    if (!r.ok) return new Map();
    const { entries } = await r.json();
    const logs = new Map();
    for (const v of entries) {
      const text = await fetchFirst([`devlog/${v}.md`, RAW + `docs/devlog/${v}.md`]);
      if (text) logs.set(v, text);
    }
    return logs;
  } catch (e) { return new Map(); }
}

function devlogDetails(version, text, open) {
  return `<details class="devlog"${open ? " open" : ""}>
    <summary>devlog — the story behind ${version}</summary>
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

async function loadReleases() {
  const el = document.getElementById("releases-body");
  const logs = await loadDevlogs();

  let releases = [];
  try {
    const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases`);
    if (r.ok) releases = await r.json();
  } catch (e) { /* offline or rate-limited: fall through to empty state */ }

  const parts = [];

  for (const rel of releases) {
    const tag = rel.tag_name || "";
    const assets = (rel.assets || []).map(a =>
      `<a class="asset" href="${a.browser_download_url}">${esc(a.name)} · ${fmtSize(a.size)}</a>`
    ).join("");
    parts.push(`<article class="release">
      <div class="release-head">
        <h3>${esc(rel.name || tag)}</h3>
        <span class="release-date mono">${tag} · ${fmtDate(rel.published_at)}</span>
      </div>
      <div class="md">${md(rel.body || "")}</div>
      ${assets ? `<div class="assets">${assets}</div>` : ""}
      ${logs.has(tag) ? devlogDetails(tag, logs.get(tag), false) : ""}
    </article>`);
    logs.delete(tag);
  }

  if (!releases.length) {
    parts.push(document.getElementById("no-releases").innerHTML);
  }

  // Devlog entries with no matching release yet: the work-in-progress story.
  let first = !releases.length;
  for (const [version, text] of logs) {
    parts.push(`<article class="release">
      <div class="release-head">
        <h3>${esc(version)}</h3>
        <span class="chip next mono">in progress · unreleased</span>
      </div>
      ${devlogDetails(version, text, first)}
    </article>`);
    first = false;
  }

  el.innerHTML = parts.join("\n");
}

/* ---- hero canvas: noise in, signal out ---------------------------------- */

function heroWave() {
  const canvas = document.getElementById("wave");
  const toggle = document.getElementById("filter-toggle");
  const ctx = canvas.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

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
    ctx.shadowColor = glow ? "rgba(167,139,250,.55)" : "transparent";
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    const cx = W / 2, side = Math.min(74, H * 0.34);
    const pL = cx - side * 0.62, pR = cx + side * 0.62;

    // Incoming: signal + noise, in gray.
    trace(0, pL, x => wave(x, t) + noise(x, t), "rgba(150,160,172,.5)", 1.6);

    // The prism.
    ctx.beginPath();
    ctx.moveTo(cx, H / 2 - side * 0.72);
    ctx.lineTo(cx + side * 0.62, H / 2 + side * 0.55);
    ctx.lineTo(cx - side * 0.62, H / 2 + side * 0.55);
    ctx.closePath();
    ctx.strokeStyle = "rgba(232,234,238,.55)";
    ctx.lineWidth = 1.4;
    ctx.shadowBlur = 16;
    ctx.shadowColor = "rgba(103,232,249,.35)";
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Outgoing: clean spectrum-lit signal, or untouched noise when off.
    if (filtered) {
      const grad = ctx.createLinearGradient(pR, 0, W, 0);
      grad.addColorStop(0, "#67e8f9");
      grad.addColorStop(0.55, "#a78bfa");
      grad.addColorStop(1, "#f0abfc");
      trace(pR, W, x => wave(x, t), grad, 2, 14);
    } else {
      trace(pR, W, x => wave(x, t) + noise(x, t), "rgba(150,160,172,.5)", 1.6);
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
      ctx.fillStyle = `rgba(150,160,172,${s.a})`;
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
  resize();
  if (reduced) { t = 4; draw(); } else { frame(); }
}

heroWave();
loadRoadmap();
loadReleases();
