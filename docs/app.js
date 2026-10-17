/* Sponsor Radar dashboard. Plain JavaScript, no AI.
   - Ranks roles with TF-IDF similarity to your CV and a logistic-regression model trained on your choices.
   - Builds tailored CVs and cover letters by selecting and ordering your own CV bullets, then makes PDFs.
   - Everything personal is stored in this browser (localStorage) only. */
(() => {
"use strict";

const TZ = "Europe/London";
const STORE_KEY = "sponsorRadar.v1";
const PAGE = 30;
const VENDOR = { jspdf: "vendor/jspdf.umd.min.js", pdf: "vendor/pdf.min.js", pdfWorker: "vendor/pdf.worker.min.js", jszip: "vendor/jszip.min.js" };
const STATUSES = [["", "To review"], ["interested", "Interested"], ["applied", "Applied"], ["interview", "Interview"], ["offer", "Offer"], ["rejected", "Rejected"], ["notforme", "Not for me"]];
const STATUS_LABEL = Object.fromEntries(STATUSES);
const POSITIVE = new Set(["interested", "applied", "interview", "offer"]);
const CHIPS = [["new", "New"], ["review", "To review"], ["interested", "Interested"], ["applied", "Applied"], ["interview", "Interview"], ["offer", "Offer"], ["closed", "Closed"]];
const SOFT_SKILLS = new Set(["Communication", "Presentation", "Problem solving", "Stakeholder management", "Data analysis", "Reporting", "Banking", "Insurance", "Claims", "Microsoft Office", "KPIs"]);
const DEFAULT_TEMPLATE = [
  "I am writing to apply for the {role} role at {company}. I hold {degree}, and my experience with {skills} matches what you are looking for.",
  "As {job1_title} at {job1_org}, I {job1_point}. I also {job1_point2}.",
  "At {job2_org}, I {job2_point}.",
  "{relocation}I would welcome the chance to bring these skills to {company} and to discuss how I could contribute to your team.",
  "{sponsorship} Thank you for considering my application.",
].join("\n\n");

// ------------------------------------------------------------------ helpers
const $ = (id) => document.getElementById(id);
const el = (tag, attrs, ...kids) => {
  const n = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
};
const str = (v) => (typeof v === "string" ? v : typeof v === "number" && isFinite(v) ? String(v) : "");
const arr = (v) => (Array.isArray(v) ? v : []);
const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const isDate = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v) && !isNaN(Date.parse(v.slice(0, 10)));
const money = (v) => (num(v) != null ? "£" + Math.round(v).toLocaleString("en-GB") : "");
const humanJoin = (xs) => (xs.length <= 1 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);
const fold = (s) => str(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

function toast(msg, action) {
  const t = $("toast");
  t.replaceChildren(el("span", null, msg));
  if (action) t.append(" ", el("button", { class: "quiet", type: "button", style: "color:inherit;text-decoration:underline;min-height:0;padding:0 4px", onclick: () => { t.hidden = true; action.fn(); } }, action.label));
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { t.hidden = true; }, action ? 6000 : 3200);
}
function setMsg(id, text, kind) { const m = $(id); if (!m) return; m.textContent = text || ""; m.className = "status-msg" + (kind ? " " + kind : ""); }

// ------------------------------------------------------------------ UK time
function ukParts(d = new Date()) {
  const f = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, hm: `${p.hour}:${p.minute}` };
}
const dayNo = (date) => { const [y, m, d] = date.slice(0, 10).split("-").map(Number); return Math.round(Date.UTC(y, m - 1, d) / 86400000); };
const today = () => ukParts().date;
function fmtDate(iso, long) {
  if (!isDate(iso)) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", { day: "numeric", month: long ? "long" : "short", year: long ? "numeric" : undefined, timeZone: "UTC" });
}
function fmtStamp(iso) {
  const t = Date.parse(iso); if (isNaN(t)) return "";
  const u = ukParts(new Date(t));
  return u.date === today() ? u.hm : `${fmtDate(u.date)}, ${u.hm}`;
}

// ------------------------------------------------------------------ storage (this browser only)
const blankStore = () => ({ v: 1, statuses: {}, snapshots: {}, docs: {}, cvRaw: "", cvOutline: "", cvSavedAt: "", details: {}, settings: { workStatus: "permission", relocate: true, template: "", chip: "review" }, visits: {} });
function normalizeStore(x) {
  const b = blankStore(), s = obj(x);
  return {
    v: 1, statuses: obj(s.statuses), snapshots: obj(s.snapshots), docs: obj(s.docs),
    cvRaw: str(s.cvRaw), cvOutline: str(s.cvOutline), cvSavedAt: str(s.cvSavedAt), details: obj(s.details),
    settings: { ...b.settings, ...obj(s.settings) }, visits: obj(s.visits), searchSaved: obj(s.searchSaved),
  };
}
function readStore() { try { return normalizeStore(JSON.parse(localStorage.getItem(STORE_KEY) || "{}")); } catch { return blankStore(); } }
let S = readStore();
function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(S)); return true; }
  catch { toast("Couldn't save in this browser (storage is full or blocked). Back up your data."); return false; }
}

// ------------------------------------------------------------------ text processing
const STOP = new Set(("a an and are as at be been by can for from has have in into is it its of on or our that the their them they this to was we were will with you your " +
  "role roles job jobs team teams work working within across including include includes such using use used able ability strong good great excellent new " +
  "join company business opportunity candidate candidates experience years year looking based help support day days time also well etc may must should " +
  "would could per who what which while where when how all any other more most some about over up out not no").split(" "));
function stem(t) {
  if (t.length > 5 && t.endsWith("ies")) return t.slice(0, -3) + "y";
  if (t.length > 5 && t.endsWith("ing")) return t.slice(0, -3);
  if (t.length > 4 && t.endsWith("ed")) return t.slice(0, -2);
  if (t.length > 3 && t.endsWith("s") && !t.endsWith("ss")) return t.slice(0, -1);
  return t;
}
function tokens(text) {
  return fold(text).replace(/[^a-z0-9+#]+/g, " ").split(" ").filter((t) => t.length > 1 && !STOP.has(t)).map(stem);
}

let SKILLS = [];
function compileSkills(list) {
  SKILLS = arr(list).map((s) => ({
    name: str(s.name),
    pats: arr(s.aliases).map((a) => new RegExp("(?<![a-z0-9])" + fold(a).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![a-z0-9])")),
    special: s.special ? new RegExp("(?:^|[\\s,(/;])" + s.special + "(?=[\\s,)/;.]|$)") : null,
  })).filter((s) => s.name);
}
function findSkills(text) {
  const low = fold(text), out = [];
  for (const s of SKILLS) if (s.pats.some((p) => p.test(low)) || (s.special && s.special.test(str(text)))) out.push(s.name);
  return out;
}

class Tfidf {
  constructor(docs) {
    this.n = docs.length; this.df = new Map();
    for (const d of docs) for (const t of new Set(d)) this.df.set(t, (this.df.get(t) || 0) + 1);
  }
  idf(t) { return Math.log((this.n + 1) / ((this.df.get(t) || 0) + 1)) + 1; }
  vec(toks) {
    const tf = new Map();
    for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1);
    const v = new Map(); let norm = 0;
    for (const [t, c] of tf) { const w = (1 + Math.log(c)) * this.idf(t); v.set(t, w); norm += w * w; }
    norm = Math.sqrt(norm) || 1;
    for (const [t, w] of v) v.set(t, w / norm);
    return v;
  }
}
function cos(a, b) {
  if (!a || !b) return 0;
  const [s, l] = a.size < b.size ? [a, b] : [b, a];
  let d = 0;
  for (const [t, w] of s) { const x = l.get(t); if (x) d += w * x; }
  return d;
}

// ------------------------------------------------------------------ CV outline
const SECTION_PATTERNS = [
  ["profile", /^(?:personal |professional |career )?(?:profile|summary|statement|objective)$|^about me$/],
  ["skills", /^(?:key |technical |core |relevant |it |professional |additional )?(?:skills|competencies|expertise|skill set)(?: (?:and|&) (?:tools|technologies|competencies|interests|qualifications))?$|^tools(?: (?:and|&) technologies)?$|^technolog(?:y|ies)$/],
  ["experience", /^(?:work |professional |relevant |employment |career |volunteer(?:ing)? |industry )?(?:experience|history|employment)(?: history)?$|^volunteering$/],
  ["education", /^(?:education|qualifications|academic (?:background|qualifications|history)|education (?:and|&) (?:qualifications|training|certifications))$/],
  ["projects", /^(?:key |academic |personal |selected |data |relevant |technical )?projects$|^portfolio$|^publications$/],
  ["certifications", /^(?:certifications?|certificates?|courses?|training|licen[cs]es(?: (?:and|&) certifications)?|certifications (?:and|&) (?:courses|training|licen[cs]es)|awards(?: (?:and|&) achievements)?|achievements|professional development)$/],
  ["languages", /^languages?(?: spoken)?$/],
  ["skip", /^(?:interests|hobbies(?: (?:and|&) interests)?|interests (?:and|&) hobbies|references|referees|additional information)$/],
];
const SECTION_OF = (h) => {
  const w = fold(h).replace(/[^a-z& ]/g, " ").replace(/\s+/g, " ").trim();
  for (const [k, re] of SECTION_PATTERNS) if (re.test(w)) return k;
  return null;
};
const SECTION_GUESS = (h) => {
  const w = fold(h).replace(/[^a-z ]/g, " ").trim().split(" ")[0] || "";
  return { profile: "profile", summary: "profile", about: "profile", skills: "skills", skill: "skills", tools: "skills", experience: "experience",
    work: "experience", employment: "experience", career: "experience", education: "education", qualifications: "education", projects: "projects",
    project: "projects", certifications: "certifications", certification: "certifications", certificates: "certifications", courses: "certifications",
    training: "certifications", awards: "certifications", languages: "languages", language: "languages" }[w] || null;
};
const MONTH = "(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
const DATE1 = `(?:${MONTH}\\.?,?\\s*)?(?:19|20)\\d{2}|\\d{1,2}\\/(?:19|20)\\d{2}`;
const RANGE_RE = new RegExp(`(${DATE1})\\s*(?:–|—|-|to|until)\\s*(${DATE1}|present|current|now|date|ongoing)`, "i");
const YEAR_RE = new RegExp(`\\b(?:${MONTH}\\.?\\s*)?(?:19|20)\\d{2}\\b`, "i");
const BULLET_RE = /^\s*(?:[•▪●◦‣∙·*–—-]|\d{1,2}[.)])\s+/;
const looksLikeDates = (s) => RANGE_RE.test(s) || /^\s*(?:\(?\s*)?(?:(?:19|20)\d{2}|present)\s*\)?\s*$/i.test(s) || (YEAR_RE.test(s) && s.replace(YEAR_RE, "").replace(/[^a-z]/gi, "").length <= 3);

function splitItems(s, seps = ",;|•·") {
  const out = []; let depth = 0, cur = "";
  for (const ch of str(s)) {
    if (ch === "(" || ch === "[") depth++;
    if ((ch === ")" || ch === "]") && depth) depth--;
    if (!depth && seps.includes(ch)) { if (cur.trim()) out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
function parseOutline(text) {
  const cv = { profile: "", skills: [], experience: [], education: [], projects: [], certifications: [], languages: [] };
  let sec = null, cur = null;
  const lines = str(text).replace(/\r/g, "").split("\n");
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    let m;
    if ((m = line.match(/^#\s+(.+)$/))) { sec = SECTION_OF(m[1]) || SECTION_GUESS(m[1]) || "skip"; cur = null; continue; }
    if ((m = line.match(/^##\s+(.+)$/))) {
      if (!["experience", "education", "projects"].includes(sec)) continue;
      const parts = m[1].split("|").map((x) => x.trim());
      let dates = "";
      if (parts.length > 1 && looksLikeDates(parts[parts.length - 1])) dates = parts.pop();
      if (sec === "projects") cur = { name: parts[0] || "", dates, bullets: [] };
      else if (sec === "experience") cur = { title: parts[0] || "", org: parts[1] || "", place: parts.slice(2).join(", "), dates, bullets: [] };
      else cur = { degree: parts[0] || "", school: parts[1] || "", place: parts.slice(2).join(", "), dates, bullets: [] };
      cv[sec].push(cur);
      continue;
    }
    const isBullet = BULLET_RE.test(line);
    const body = line.replace(BULLET_RE, "").trim();
    if (sec === "profile") cv.profile = (cv.profile ? cv.profile + " " : "") + body;
    else if (sec === "skills") {
      const mm = body.match(/^([^:]{2,40}):\s*(.+)$/);
      const items = splitItems(mm ? mm[2] : body);
      if (mm) cv.skills.push({ label: mm[1].trim(), items });
      else {
        let g = cv.skills.find((x) => x.label === "Skills");
        if (!g) { g = { label: "Skills", items: [] }; cv.skills.push(g); }
        g.items.push(...items);
      }
    } else if (sec === "certifications" || sec === "languages") {
      cv[sec].push(...splitItems(body, ";|•·"));
    } else if (["experience", "education", "projects"].includes(sec)) {
      if (!cur) { cur = sec === "projects" ? { name: "", dates: "", bullets: [] } : sec === "experience" ? { title: "", org: "", place: "", dates: "", bullets: [] } : { degree: "", school: "", place: "", dates: "", bullets: [] }; cv[sec].push(cur); }
      if (isBullet || !cur.bullets.length) cur.bullets.push(body);
      else cur.bullets[cur.bullets.length - 1] += " " + body;
    }
  }
  return cv;
}
function outlineHasContent(cv) { return !!(cv.experience.length || cv.education.length || cv.skills.length || cv.profile); }

function rawToOutline(rawText) {
  const lines = str(rawText).replace(/\r/g, "").split("\n").map((l) => l.replace(/\s+/g, " ").trim());
  const details = {};
  const head = [];
  const out = { profile: [], skills: [], experience: [], education: [], projects: [], certifications: [], languages: [] };
  let sec = null, cur = null, pending = [];
  const email = str(rawText).match(/[\w.+-]+@[\w-]+\.[\w.-]+/); if (email) details.email = email[0];
  const phone = str(rawText).match(/(?:\+44\s?\d{4}|\(?0\d{4}\)?)\s?\d{3}\s?\d{3,4}|\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,4}[\s-]?\d{3,4}/); if (phone) details.phone = phone[0].trim();
  const li = str(rawText).match(/(?:https?:\/\/)?(?:www\.)?linkedin\.com\/in\/[\w-]+\/?/i); if (li) details.linkedin = li[0].replace(/^https?:\/\/(www\.)?/i, "");

  const newEntry = (texts, dates) => {
    const parts = texts.flatMap((t) => t.split(/\s*[|•·]\s*|\s+[–—]\s+|\s+-\s+|,\s+(?=[A-Z])|\s+at\s+/)).map((x) => x.trim()).filter(Boolean);
    if (sec === "projects") cur = { name: parts.join(" – "), dates, bullets: [] };
    else cur = { a: parts[0] || "", b: parts[1] || "", c: parts.slice(2).join(", "), dates, bullets: [] };
    out[sec].push(cur);
  };
  const flushPending = () => {
    if (!pending.length) return;
    if (cur && cur.bullets.length) cur.bullets.push(...pending);
    else if (cur && !cur.b && sec !== "projects") { const p = pending.join(", ").split(/,\s+/); cur.b = p[0]; cur.c = [cur.c, ...p.slice(1)].filter(Boolean).join(", "); }
    else newEntry(pending, "");
    pending = [];
  };

  for (const line of lines) {
    if (!line) continue;
    const plain = line.replace(/:$/, "");
    const s = plain.length <= 45 && !BULLET_RE.test(line) ? SECTION_OF(plain) : null;
    if (s && (plain === plain.toUpperCase() || plain.split(" ").length <= 4)) {
      if (["experience", "education", "projects"].includes(sec)) flushPending();
      sec = s; cur = null; pending = [];
      continue;
    }
    if (!sec) { head.push(line); continue; }
    if (sec === "skip") continue;
    const isBullet = BULLET_RE.test(line);
    const body = line.replace(BULLET_RE, "").trim();
    if (sec === "profile") { out.profile.push(body); continue; }
    if (sec === "skills") { out.skills.push(body); continue; }
    if (sec === "certifications" || sec === "languages") { out[sec].push(body); continue; }
    // experience / education / projects
    const range = line.match(RANGE_RE);
    const dateOnly = !isBullet && looksLikeDates(line);
    if (!isBullet && (range || dateOnly || (sec !== "experience" && YEAR_RE.test(line) && line.length < 90))) {
      const m = range || line.match(YEAR_RE);
      const dates = m ? m[0].trim() : "";
      const rest = line.replace(dates, "").replace(/[\s,|–—()-]+$/, "").replace(/^[\s,|–—()-]+/, "").trim();
      const texts = [...pending, rest].filter(Boolean);
      pending = [];
      if (!texts.length && cur && !cur.dates) { cur.dates = dates; continue; }
      newEntry(texts, dates);
      continue;
    }
    if (isBullet) {
      if (pending.length) {
        if (!cur || cur.bullets.length) { newEntry(pending, ""); pending = []; }
        else flushPending();
      }
      if (!cur) newEntry(["Untitled"], "");
      cur.bullets.push(body);
      continue;
    }
    // a plain line: a wrapped bullet, or the start of the next entry
    const last = cur && cur.bullets.length ? cur.bullets[cur.bullets.length - 1] : null;
    const titleLike = /^[A-Z]/.test(line) && line.split(" ").length <= 8 && !/[.!?]$/.test(line);
    if (last != null && !pending.length && (/^[a-z0-9(&%£$,]/.test(line) || (!/[.!?]$/.test(last) && !titleLike))) {
      cur.bullets[cur.bullets.length - 1] = last + " " + line;
      continue;
    }
    if (cur && !cur.bullets.length && !cur.b && !pending.length && sec !== "projects") {
      const p = line.split(/\s*[|•·]\s*|,\s+/); cur.b = p[0]; cur.c = [cur.c, ...p.slice(1)].filter(Boolean).join(", ");
      continue;
    }
    pending.push(line);
  }
  if (["experience", "education", "projects"].includes(sec)) flushPending();

  // name: the first short line of the header without contact details
  const nameLine = head.find((l) => l.length <= 40 && !/[@\d]|linkedin|http/i.test(l) && l.split(" ").length <= 5);
  if (nameLine) details.name = nameLine.replace(/\b([a-z])/g, (c) => c.toUpperCase()).replace(/\b([A-Z])([A-Z]+)\b/g, (m, a, b) => a + b.toLowerCase());
  const place = head.flatMap((l) => l.split(/\s*[|•·]\s*/)).find((p) => /^[A-Za-z .'-]+,\s*(UK|United Kingdom|England|Scotland|Wales|Northern Ireland)$/i.test(p));
  if (place) details.home = place.split(",")[0].trim();
  if (!out.profile.length) {
    const para = head.filter((l) => l !== nameLine && l.length > 80 && !/[@]|linkedin/i.test(l));
    if (para.length) out.profile.push(...para);
  }

  const L = [];
  if (out.profile.length) L.push("# Profile", out.profile.join(" "), "");
  if (out.skills.length) {
    L.push("# Skills");
    for (const s of out.skills) L.push(/^[^:]{2,40}:\s*\S/.test(s) ? s : splitItems(s, ";|•·").join(", "));
    L.push("");
  }
  const block = (name, list, fmt) => {
    if (!list.length) return;
    L.push("# " + name);
    for (const e of list) { L.push("## " + fmt(e)); for (const b of e.bullets) L.push("- " + b); L.push(""); }
  };
  block("Experience", out.experience, (e) => [e.a, e.b, e.c, e.dates].filter(Boolean).join(" | "));
  block("Education", out.education, (e) => [e.a, e.b, e.c, e.dates].filter(Boolean).join(" | "));
  block("Projects", out.projects, (e) => [e.name, e.dates].filter(Boolean).join(" | "));
  if (out.certifications.length) { L.push("# Certifications", ...out.certifications.map((x) => "- " + x), ""); }
  if (out.languages.length) { L.push("# Languages", ...out.languages.map((x) => "- " + x), ""); }
  return { outline: L.join("\n").trim() + "\n", details };
}

function cvAsText(cv) {
  const parts = [cv.profile];
  for (const g of cv.skills) parts.push(g.label + ": " + g.items.join(", "));
  for (const e of cv.experience) parts.push(e.title, e.org, ...e.bullets);
  for (const e of cv.education) parts.push(e.degree, e.school, ...e.bullets);
  for (const p of cv.projects) parts.push(p.name, ...p.bullets);
  parts.push(...cv.certifications);
  return parts.filter(Boolean).join("\n");
}
let CV = null, CV_SKILLS = new Set();
function loadCv() {
  CV = S.cvOutline.trim() ? parseOutline(S.cvOutline) : null;
  if (CV && !outlineHasContent(CV)) CV = null;
  CV_SKILLS = new Set(CV ? findSkills(cvAsText(CV)) : []);
}

// ------------------------------------------------------------------ data
let JOBS = [], META = {}, BY_ID = new Map(), TF = null, LOAD_ERROR = null;
async function getJson(url) {
  const r = await fetch(url + "?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
function sanitizeJob(j) {
  const o = obj(j);
  if (!str(o.id) || !str(o.title)) return null;
  return {
    id: o.id, title: str(o.title), company: str(o.company), location: str(o.location), source: str(o.source),
    url: /^https?:\/\//.test(str(o.url)) ? o.url : "", links: arr(o.links).map(obj).filter((l) => /^https?:\/\//.test(str(l.url))),
    salary: obj(o.salary), posted: isDate(o.posted) ? o.posted : "", closes: isDate(o.closes) ? o.closes : "",
    first_seen: str(o.first_seen), family: str(o.family), relevance: num(o.relevance) ?? 0,
    sponsor: obj(o.sponsor), flags: arr(o.flags).map(str).filter(Boolean), score: num(o.score) ?? 0,
    desc: str(o.desc), skills: arr(o.skills).map(str).filter(Boolean), full_text: !!o.full_text,
  };
}

// ------------------------------------------------------------------ preference model (logistic regression)
const DIM = 4096;
const NUM_NAMES = ["CV fit", "sponsorship evidence", "title match", "salary meets the minimum", "based in London", "remote or hybrid"];
const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0) % DIM; };
function featuresOf(item) {
  const sparse = new Map(), names = new Map();
  const add = (name, v) => { const i = hash(name); sparse.set(i, (sparse.get(i) || 0) + v); if (!names.has(i)) names.set(i, name); };
  for (const t of new Set(tokens(item.title))) add("t:" + t, 1);
  for (const s of item.skills || []) add("s:" + s, 0.7);
  if (item.company) add("c:" + fold(item.company), 1);
  if (item.family) add("f:" + item.family, 0.8);
  const loc = fold(item.location);
  const sal = obj(item.salary);
  const fit = item._fitPct != null ? item._fitPct / 100 : (num(item.fitPct) != null ? item.fitPct / 100 : 0.5);
  const numf = [fit, num(obj(item.sponsor).score) ?? 0.5, num(item.relevance) ?? 0.5, sal.ok === true ? 1 : sal.ok === false ? 0 : 0.5,
    /london/.test(loc) ? 1 : 0, /remote|hybrid/.test(loc + " " + fold(item.title)) ? 1 : 0];
  return { sparse, num: numf, names };
}
const PREFS = {
  ready: false, n: 0, pos: 0, neg: 0, w: null, wn: null, b: 0, names: new Map(),
  train(examples) {
    this.n = examples.length; this.pos = examples.filter((e) => e.y === 1).length; this.neg = this.n - this.pos;
    this.ready = this.pos >= 3 && this.neg >= 3;
    if (!this.ready) return;
    const w = new Float64Array(DIM), wn = new Float64Array(NUM_NAMES.length);
    let b = 0;
    const n = this.n, c1 = n / (2 * this.pos), c0 = n / (2 * this.neg), lr = 0.5, l2 = 0.01;
    this.names = new Map();
    for (const e of examples) for (const [i, name] of e.x.names) if (!this.names.has(i)) this.names.set(i, name);
    for (let epoch = 0; epoch < 300; epoch++) {
      const gw = new Map(), gn = new Float64Array(wn.length); let gb = 0;
      for (const e of examples) {
        let z = b; for (const [i, v] of e.x.sparse) z += w[i] * v; for (let k = 0; k < wn.length; k++) z += wn[k] * e.x.num[k];
        const p = 1 / (1 + Math.exp(-z)), g = (p - e.y) * (e.y ? c1 : c0);
        for (const [i, v] of e.x.sparse) gw.set(i, (gw.get(i) || 0) + g * v);
        for (let k = 0; k < wn.length; k++) gn[k] += g * e.x.num[k];
        gb += g;
      }
      for (const [i, g] of gw) w[i] -= lr * (g / n);
      for (let i = 0; i < DIM; i++) if (w[i]) w[i] -= lr * l2 * w[i];
      for (let k = 0; k < wn.length; k++) wn[k] -= lr * (gn[k] / n + l2 * wn[k]);
      b -= lr * (gb / n);
    }
    this.w = w; this.wn = wn; this.b = b;
  },
  predict(x) {
    if (!this.ready) return null;
    let z = this.b; for (const [i, v] of x.sparse) z += this.w[i] * v; for (let k = 0; k < this.wn.length; k++) z += this.wn[k] * x.num[k];
    return 1 / (1 + Math.exp(-z));
  },
  explain(k = 6) {
    if (!this.ready) return { up: [], down: [] };
    const items = [];
    for (const [i, name] of this.names) if (Math.abs(this.w[i]) > 0.02) items.push([name, this.w[i]]);
    NUM_NAMES.forEach((nm, k2) => items.push(["n:" + nm, this.wn[k2]]));
    items.sort((a, b) => b[1] - a[1]);
    const label = ([name]) => {
      const [kind, ...rest] = name.split(":"); const v = rest.join(":");
      return kind === "t" ? `"${v}" in the job title` : kind === "s" ? `asks for ${v}` : kind === "c" ? `employer: ${v}` : kind === "f" ? `target: ${v}` : v;
    };
    return { up: items.filter((x) => x[1] > 0.05).slice(0, k).map(label), down: items.filter((x) => x[1] < -0.05).reverse().slice(0, k).map(label) };
  },
};

// ------------------------------------------------------------------ ranking
function itemFor(id) { return BY_ID.get(id) || (S.snapshots[id] ? { ...S.snapshots[id], id, _gone: true } : null); }
function rankAll() {
  loadCv();
  const docs = JOBS.map((j) => (j._tokens = tokens(`${j.title} ${j.title} ${j.title} ${j.skills.join(" ")} ${j.skills.join(" ")} ${j.desc}`)));
  TF = new Tfidf(docs.concat(CV ? [tokens(cvAsText(CV))] : []));
  for (const j of JOBS) j._vec = TF.vec(j._tokens);
  if (CV) {
    const v = TF.vec(tokens(cvAsText(CV)));
    for (const j of JOBS) j._fit = cos(v, j._vec);
    const sorted = JOBS.map((j) => j._fit).sort((a, b) => a - b);
    const n = sorted.length;
    for (const j of JOBS) {
      const lo = sorted.indexOf(j._fit), hi = sorted.lastIndexOf(j._fit);
      j._fitPct = n > 1 ? Math.round(((lo + hi) / 2 / (n - 1)) * 100) : 100;
    }
  } else for (const j of JOBS) { j._fit = null; j._fitPct = null; }
  const examples = [];
  for (const [id, st] of Object.entries(S.statuses)) {
    const s = obj(st).s;
    if (!POSITIVE.has(s) && s !== "notforme") continue;
    const it = itemFor(id); if (!it) continue;
    examples.push({ x: featuresOf(it), y: POSITIVE.has(s) ? 1 : 0 });
  }
  PREFS.train(examples);
  for (const j of JOBS) {
    j._p = PREFS.ready ? PREFS.predict(featuresOf(j)) : null;
    let r = j.score;
    if (j._fitPct != null) r = 0.5 * r + 0.5 * (j._fitPct / 100);
    if (j._p != null) { const w = Math.min(0.6, PREFS.n / 40); r = (1 - w) * r + w * j._p; }
    j._rank = r;
  }
}

// ------------------------------------------------------------------ statuses
const statusOf = (id) => str(obj(S.statuses[id]).s);
function snapshotOf(j) {
  return { title: j.title, company: j.company, location: j.location, url: j.url, family: j.family, source: j.source,
    salary: { text: str(obj(j.salary).text), ok: obj(j.salary).ok ?? null, minimum: obj(j.salary).minimum ?? null },
    closes: j.closes, first_seen: j.first_seen, skills: j.skills.slice(0, 20), relevance: j.relevance,
    sponsor: { level: str(obj(j.sponsor).level), score: num(obj(j.sponsor).score) }, fitPct: j._fitPct ?? null, desc: j.desc.slice(0, 1500) };
}
function setStatus(id, s, quiet) {
  const it = itemFor(id); if (!it) return;
  const prev = S.statuses[id] ? { ...S.statuses[id] } : null;
  if (!s) { delete S.statuses[id]; if (!S.docs[id]) delete S.snapshots[id]; }
  else {
    const st = { ...obj(S.statuses[id]), s, at: today() };
    if (["applied", "interview", "offer"].includes(s) && !st.appliedOn) st.appliedOn = today();
    S.statuses[id] = st;
    if (!it._gone) S.snapshots[id] = snapshotOf(it);
  }
  save(); rankAll(); renderAll();
  if (!quiet) toast(s ? `Marked as ${STATUS_LABEL[s]}` : "Moved back to To review", { label: "Undo", fn: () => { if (prev) S.statuses[id] = prev; else delete S.statuses[id]; save(); rankAll(); renderAll(); } });
}

// ------------------------------------------------------------------ rendering: header, notices, start
function renderHeader() {
  const g = META.generated_at;
  const dot = $("runDot"), text = $("runText");
  if (LOAD_ERROR) { dot.className = "dot stale"; text.textContent = "Couldn't load the latest search results. Reload the page to try again."; return; }
  if (!g) { dot.className = "dot"; text.textContent = "Waiting for the first search to finish."; return; }
  const ageH = (Date.now() - Date.parse(g)) / 3600000;
  dot.className = "dot " + (ageH > 6 ? "stale" : "ok");
  const c = obj(META.counts);
  const bits = [`Last search ${fmtStamp(g)}`, `${c.checked ?? 0} new listing${c.checked === 1 ? "" : "s"} checked`, `${c.kept ?? 0} kept`, `${JOBS.length} on the radar`];
  text.replaceChildren(bits.join(" · "));
  const links = [];
  if (META.run_url) links.push(el("a", { href: META.run_url, target: "_blank", rel: "noopener" }, "Run log"));
  if (META.repo) links.push(el("a", { href: `https://github.com/${META.repo}/blob/main/config.yml`, target: "_blank", rel: "noopener" }, "Settings"));
  if (links.length) text.append(" · ", ...links.flatMap((l, i) => (i ? [" · ", l] : [l])));
  $("footRun").textContent = `Searches ${META.schedule || "every 2 hours"}. Last search ${fmtStamp(g)} (UK time).`;
}
function renderNotices() {
  const box = $("notices"), out = [];
  const add = (kind, ...kids) => out.push(el("div", { class: "notice " + kind }, ...kids));
  const repo = META.repo;
  const secretsLink = repo ? el("a", { href: `https://github.com/${repo}/settings/secrets/actions`, target: "_blank", rel: "noopener" }, "repository secrets") : "repository secrets";
  if (LOAD_ERROR) add("bad", "The search results couldn't be loaded (", LOAD_ERROR, ").");
  else if (!META.generated_at) add("warn", el("strong", null, "No search has finished yet. "), "It runs every 2 hours. To run it now, open Actions in your repository, choose Sponsor Radar and press Run workflow.");
  const src = obj(META.sources);
  for (const [key, label] of [["adzuna", "Adzuna"], ["reed", "Reed"]]) {
    const s = obj(src[key]);
    if (s.skipped && /API key/i.test(s.skipped)) add("warn", el("strong", null, `${label} isn't searched yet: `), `add its key to the `, secretsLink, ".");
    else if (s.error) add("bad", el("strong", null, `${label}: `), s.error);
  }
  const reg = obj(src.register);
  if (reg.ok === false) add("bad", el("strong", null, "Sponsor register: "), `couldn't be downloaded this time (${str(reg.error)}). Roles are judged again on the next run.`);
  for (const p of arr(obj(META.rules).changed)) {
    add("warn", el("strong", null, "Visa rules page updated: "), `gov.uk changed "${str(p.title)}" on ${fmtDate(p.updated, true)}. Check the salary minimums against it, then update them `,
      repo ? el("a", { href: `https://github.com/${repo}/edit/main/config.yml`, target: "_blank", rel: "noopener" }, "in config.yml") : "in config.yml", ". ",
      el("a", { href: str(p.url), target: "_blank", rel: "noopener" }, "Open the page"));
  }
  const ch = obj(obj(META.alerts).channels);
  if (ch.github && ch.github.ok === false) add("warn", el("strong", null, "Alerts: "), "GitHub notifications couldn't be posted: " + str(ch.github.error));
  if (META.generated_at && (Date.now() - Date.parse(META.generated_at)) / 3600000 > 6)
    add("warn", el("strong", null, "The last search was a while ago. "), "GitHub may have paused the schedule. Check Actions in your repository.");
  box.replaceChildren(...out);
}
function renderStart() {
  const cvDone = !!CV, detailsDone = !!(str(S.details.name) && str(S.details.email));
  const marks = Object.values(S.statuses).filter((x) => POSITIVE.has(obj(x).s) || obj(x).s === "notforme").length;
  const marksDone = marks >= 6;
  for (const [id, done] of [["stepCv", cvDone], ["stepDetails", detailsDone], ["stepMarks", marksDone]]) {
    $(id).classList.toggle("done", done); $(id).querySelector(".tick").textContent = done ? "✓" : "";
  }
  $("start").hidden = cvDone && detailsDone && marksDone;
}

// ------------------------------------------------------------------ rendering: roles
const UI = { chip: ["new", "review", "interested", "applied", "interview", "offer", "closed"].includes(S.settings.chip) ? S.settings.chip : "review", q: "", family: "", sponsor: "all", salaryOnly: false, sort: "best", shown: PAGE };
const isNew = (j) => {
  const t = Date.parse(j.first_seen); if (isNaN(t)) return false;
  const prev = Date.parse(S.visits.prev || "");
  return isNaN(prev) ? Date.now() - t < 86400000 : t > prev;
};
function chipOf(it) {
  const s = statusOf(it.id);
  if (!s) return "review";
  if (s === "rejected" || s === "notforme") return "closed";
  return s;
}
function allItems() {
  const items = [...JOBS];
  for (const id of Object.keys(S.statuses)) if (!BY_ID.has(id) && S.snapshots[id]) items.push({ ...sanitizeSnapshot(S.snapshots[id]), id, _gone: true });
  return items;
}
function sanitizeSnapshot(s) {
  const o = obj(s);
  return { title: str(o.title) || "Role", company: str(o.company), location: str(o.location), url: str(o.url), family: str(o.family), source: str(o.source),
    salary: obj(o.salary), closes: str(o.closes), first_seen: str(o.first_seen), skills: arr(o.skills).map(str), relevance: num(o.relevance) ?? 0,
    sponsor: obj(o.sponsor), fitPct: num(o.fitPct), desc: str(o.desc), flags: [], links: [], score: 0, _rank: -1 };
}
function filtered() {
  const q = fold(UI.q).trim();
  let list = allItems().filter((it) => {
    const c = chipOf(it);
    if (UI.chip === "new") { if (c !== "review" || it._gone || !isNew(it)) return false; }
    else if (c !== UI.chip) return false;
    if (UI.family && it.family !== UI.family) return false;
    if (UI.sponsor === "Likely" && str(obj(it.sponsor).level) !== "Likely") return false;
    if (UI.salaryOnly && obj(it.salary).ok !== true) return false;
    if (q && !fold([it.title, it.company, it.location, it.skills.join(" "), it.desc].join(" ")).includes(q)) return false;
    return true;
  });
  const by = {
    best: (a, b) => (b._rank ?? -1) - (a._rank ?? -1),
    newest: (a, b) => str(b.first_seen).localeCompare(str(a.first_seen)),
    closing: (a, b) => (a.closes || "9999").localeCompare(b.closes || "9999"),
    salary: (a, b) => (num(obj(b.salary).max) ?? num(obj(b.salary).min) ?? 0) - (num(obj(a.salary).max) ?? num(obj(a.salary).min) ?? 0),
  }[UI.sort] || ((a, b) => 0);
  return list.sort(by);
}
function renderChips() {
  const counts = Object.fromEntries(CHIPS.map(([k]) => [k, 0]));
  for (const it of allItems()) { const c = chipOf(it); counts[c]++; if (c === "review" && !it._gone && isNew(it)) counts.new++; }
  $("chips").replaceChildren(...CHIPS.map(([k, label]) => el("button", { class: "chip", type: "button", "aria-pressed": String(UI.chip === k),
    onclick: () => { UI.chip = k; UI.shown = PAGE; S.settings.chip = k; save(); renderRoles(); } }, label, el("span", { class: "n" }, String(counts[k])))));
  const fams = [...new Set(arr(META.targets).map((t) => str(obj(t).name)).concat(JOBS.map((j) => j.family)).filter(Boolean))];
  const ff = $("familyFilter");
  ff.replaceChildren(el("option", { value: "" }, "All targets"), ...fams.map((f) => el("option", { value: f }, f)));
  ff.value = fams.includes(UI.family) ? UI.family : "";
}
function meter(label, pct, title) {
  return el("div", { class: "meter", title: title || null }, el("span", null, label),
    el("div", { class: "bar", role: "img", "aria-label": `${label}: ${Math.round(pct)}%` }, el("i", { style: `width:${Math.max(3, Math.min(100, pct))}%` })));
}
function fitLabel(p) { return p >= 75 ? "Strong fit with your CV" : p >= 40 ? "Good fit with your CV" : "Weaker fit with your CV"; }
function closingBits(it) {
  if (!it.closes) return el("span", null, "No closing date given");
  const left = dayNo(it.closes) - dayNo(today());
  const cls = left <= 3 ? "tag bad" : left <= 7 ? "tag warn" : "";
  return el("span", { class: cls || null }, left < 0 ? `Closed ${fmtDate(it.closes)}` : left === 0 ? "Closes today" : `Closes ${fmtDate(it.closes)} (${left} day${left === 1 ? "" : "s"})`);
}
function salaryBits(it) {
  const s = obj(it.salary), min = num(s.minimum);
  if (s.ok === true) return el("span", null, `${str(s.text)} `, el("span", { class: "tag good" }, `meets ${money(min)}`));
  if (s.ok === false) return el("span", null, `${str(s.text)} `, el("span", { class: "tag bad" }, `below ${money(min)}`));
  return el("span", null, "Salary not stated ", min ? el("span", { class: "tag warn" }, `confirm ${money(min)}+`) : null);
}
function card(it) {
  const s = statusOf(it.id), sp = obj(it.sponsor), level = str(sp.level);
  const actions = el("div", { class: "card-actions" },
    it.url ? el("a", { class: "btn primary", href: it.url, target: "_blank", rel: "noopener" }, "Apply") : null,
    el("button", { type: "button", onclick: () => openDocs(it.id) }, "CV & letter"),
    s !== "interested" && !["applied", "interview", "offer"].includes(s) ? el("button", { type: "button", onclick: () => setStatus(it.id, "interested") }, "Interested") : null,
    s !== "notforme" ? el("button", { type: "button", class: "quiet", onclick: () => setStatus(it.id, "notforme") }, "Not for me") : null,
    el("select", { "aria-label": `Status for ${it.title} at ${it.company}`, onchange: (e) => setStatus(it.id, e.target.value) },
      STATUSES.map(([k, l]) => el("option", { value: k, selected: k === s }, l))),
    it.source === "Adzuna" ? el("a", { class: "adzuna", href: "https://www.adzuna.co.uk/", target: "_blank", rel: "noopener" }, "Jobs by Adzuna") : null);
  const top = el("div", { class: "card-top" },
    el("div", { style: "min-width:0; display:grid; gap:2px" }, el("h3", null, it.title), el("span", null, [it.company, it.location].filter(Boolean).join(" · "))),
    el("div", { class: "badges" },
      !it._gone && isNew(it) && !s ? el("span", { class: "tag accent" }, "New") : null,
      level ? el("span", { class: "tag " + (level === "Likely" ? "good" : "warn"), title: arr(sp.reasons).join("\n") || null }, `${level} sponsor`) : null,
      s ? el("span", { class: "tag accent" }, STATUS_LABEL[s]) : null));
  const facts = el("div", { class: "facts" }, salaryBits(it), closingBits(it), it.family ? el("span", null, it.family) : null,
    it.posted ? el("span", null, `Posted ${fmtDate(it.posted)}`) : null);
  if (it._gone) {
    return el("article", { class: "card st-" + (s || "review") }, top,
      el("p", { class: "small muted" }, "No longer on the radar (closed, or found more than 30 days ago). Your status and documents are kept."), actions);
  }
  const meters = el("div", { class: "meters" },
    it._fitPct != null ? meter(fitLabel(it._fitPct), it._fitPct, "Compared with the other roles on the radar") : null,
    it._p != null ? meter(`Your taste: ${Math.round(it._p * 100)}%`, it._p * 100, "Learned from the roles you marked") : null,
    meter(`Sponsorship evidence: ${Math.round((num(sp.score) ?? 0) * 100)}%`, (num(sp.score) ?? 0) * 100));
  const skills = it.skills.length ? el("div", { class: "skills", "aria-label": "Skills in the advert" },
    it.skills.slice(0, 14).map((k) => el("span", { class: "sk" + (CV_SKILLS.has(k) ? " have" : ""), title: CV_SKILLS.has(k) ? "In your CV" : null }, k))) : null;
  const why = [...arr(sp.reasons), `Title matches "${it.family}" (${Math.round((it.relevance || 0) * 100)}%)`, ...it.flags];
  const details = el("details", null, el("summary", null, "Why it's here, and the advert"),
    el("ul", { class: "why" }, why.map((w) => el("li", null, w))),
    it.desc ? el("div", { class: "desc", style: "margin-top:8px" }, it.desc) : null,
    el("p", { class: "small", style: "margin-top:8px" }, it.links.map((l, i) => [i ? " · " : "", el("a", { href: l.url, target: "_blank", rel: "noopener" }, `View on ${l.source}`)])));
  return el("article", { class: "card st-" + (s || "review") }, top, facts, meters, skills, actions, details);
}
function renderRoles() {
  renderChips();
  const list = filtered();
  $("rolesCount").textContent = `${list.length} shown`;
  const cards = $("cards");
  if (!JOBS.length && !Object.keys(S.statuses).length) {
    cards.replaceChildren(el("p", { class: "empty" }, META.generated_at ? "No sponsored roles on the radar right now. New listings are checked every 2 hours." : "Roles appear here after the first search finishes."));
    $("more").hidden = true; return;
  }
  if (!list.length) {
    const msg = UI.chip === "new" ? "Nothing new since your last visit." : UI.chip === "review" ? "Nothing left to review. New roles appear as they're found." : "No roles here yet.";
    cards.replaceChildren(el("p", { class: "empty" }, msg)); $("more").hidden = true; return;
  }
  cards.replaceChildren(...list.slice(0, UI.shown).map(card));
  const more = $("more"); more.hidden = list.length <= UI.shown;
  more.textContent = `Show more (${list.length - UI.shown} left)`;
}

// ------------------------------------------------------------------ rendering: model, CV preview
function renderModel() {
  const box = $("modelBody");
  const ex = PREFS.explain();
  const parts = [
    el("p", null, "Each search keeps only roles at employers on the Home Office sponsor register (or adverts that offer sponsorship), and drops adverts that rule out sponsorship, need security clearance, are too senior or pay under the visa minimum. The rest are ranked by:"),
    el("ul", null,
      el("li", null, el("strong", null, "Title match and sponsorship evidence"), ", from the search itself."),
      el("li", null, el("strong", null, "Fit with your CV"), ": TF-IDF text similarity between your CV and each advert", CV ? "." : " (add your CV to turn this on)."),
      el("li", null, el("strong", null, "Your taste"), ": a logistic-regression model trained in this browser on the roles you mark as Interested or Applied versus Not for me.")),
  ];
  if (PREFS.ready) {
    parts.push(el("p", null, `Learned from ${PREFS.n} of your choices (${PREFS.pos} liked, ${PREFS.neg} passed). It counts for ${Math.round(Math.min(0.6, PREFS.n / 40) * 100)}% of the ranking and grows as you mark more.`));
    parts.push(el("div", { class: "weights" },
      el("div", null, el("h4", null, "Pushes roles up"), ex.up.length ? el("ul", null, ex.up.map((x) => el("li", null, x))) : el("p", { class: "small muted" }, "Nothing strong yet.")),
      el("div", null, el("h4", null, "Pushes roles down"), ex.down.length ? el("ul", null, ex.down.map((x) => el("li", null, x))) : el("p", { class: "small muted" }, "Nothing strong yet."))));
  } else {
    parts.push(el("p", { class: "muted" }, `Mark at least 3 roles as Interested (or Applied) and 3 as Not for me, and the ranking starts learning what you like. So far: ${PREFS.pos} liked, ${PREFS.neg} passed.`));
  }
  box.replaceChildren(...parts);
}
function renderCvPreview() {
  const box = $("cvPreview");
  $("cvSaved").textContent = S.cvSavedAt ? `Saved ${fmtStamp(S.cvSavedAt)}` : "";
  if (!CV) { box.replaceChildren(el("p", { class: "muted" }, "Save your CV outline to see it here.")); return; }
  const blocks = [];
  if (CV.profile) blocks.push(el("div", null, el("h4", null, "Profile"), el("p", null, CV.profile)));
  if (CV.skills.length) blocks.push(el("div", null, el("h4", null, "Skills"), el("ul", null, CV.skills.map((g) => el("li", null, el("strong", null, g.label + ": "), g.items.join(", "))))));
  const ent = (title, list, a, b) => list.length && blocks.push(el("div", null, el("h4", null, title), list.map((e) => el("div", null,
    el("strong", null, [e[a], b ? e[b] : ""].filter(Boolean).join(" · ") || "(no title)"), e.dates ? el("span", { class: "muted" }, " · " + e.dates) : null,
    el("ul", null, e.bullets.map((x) => el("li", null, x)))))));
  ent("Experience", CV.experience, "title", "org");
  ent("Education", CV.education, "degree", "school");
  ent("Projects", CV.projects, "name", null);
  if (CV.certifications.length) blocks.push(el("div", null, el("h4", null, "Certifications"), el("p", null, CV.certifications.join(" · "))));
  if (CV.languages.length) blocks.push(el("div", null, el("h4", null, "Languages"), el("p", null, CV.languages.join(" · "))));
  const warn = [];
  if (!CV.experience.length) warn.push("No experience entries found: start each with a line like \"## Job title | Organisation | Place | Dates\".");
  if (CV.experience.some((e) => !e.bullets.length)) warn.push("Some experience entries have no bullet points.");
  if (CV.experience.some((e) => !e.title || !e.org)) warn.push("Some experience entries are missing a title or an organisation.");
  box.replaceChildren(...warn.map((w) => el("p", { class: "notice warn" }, w)), ...blocks,
    el("p", { class: "small muted" }, `Skills recognised in your CV: ${[...CV_SKILLS].join(", ") || "none yet"}.`));
}
function renderAll() { renderHeader(); renderNotices(); renderStart(); renderRoles(); renderModel(); }

// ------------------------------------------------------------------ tailoring (no AI: selection, ordering and templates)
const IRREGULAR = new Set("built led ran wrote drove made won set grew cut taught spoke met held brought began chose did found gave got kept knew learnt learned left lent oversaw put read saw sold sent spent took thought understood upheld undertook drew".split(" "));
function clause(bullet) {
  let s = str(bullet).trim().replace(/[.;:\s]+$/, "");
  if (!s) return "";
  const first = s.split(/\s+/)[0];
  const lc = /^[A-Z][a-z]/.test(first) ? s[0].toLowerCase() + s.slice(1) : s;
  if (/^[A-Za-z]+ed$/.test(first) || IRREGULAR.has(first.toLowerCase())) return lc;
  if (/^responsible$/i.test(first)) return "was " + lc;
  return "worked on " + lc;
}
const article = (w) => (/^(?:[AEIOU]|(?:M|N|S|F|H|L|R|X)(?:[A-Z]|Sc|Res|Phil|Eng|Math|A\b|BA\b))/.test(str(w).trim()) && !/^(?:Uni|Use|Eu)/.test(str(w).trim()) ? "an" : "a");
function cleanJobTitle(t) {
  return str(t).replace(/\(.*?\)|\[.*?\]/g, " ").split(/\s[-–|]\s/)[0]
    .replace(/\b(?:junior|jnr|graduate|grad|entry[- ]level|trainee|associate)\b/gi, " ").replace(/\s+/g, " ").trim() || str(t);
}
function tailor(job) {
  const jobVec = job._vec || (TF ? TF.vec(tokens(`${job.title} ${job.skills.join(" ")} ${job.desc}`)) : null);
  const jobSkills = new Set(job.skills || []);
  const score = (b) => cos(TF ? TF.vec(tokens(b)) : null, jobVec) + 0.12 * findSkills(b).filter((s) => jobSkills.has(s)).length;
  const rankBullets = (bs) => bs.map((b, i) => ({ b, s: score(b), i })).sort((a, b) => b.s - a.s || a.i - b.i);
  const exp = CV.experience.map((e) => { const r = rankBullets(e.bullets); return { e, r, best: r.length ? r[0].s : 0 }; });
  const edu = CV.education.map((e) => ({ e, r: rankBullets(e.bullets) }));
  const proj = CV.projects.map((p) => { const r = rankBullets(p.bullets); return { p, r, best: r.length ? r[0].s : 0 }; })
    .sort((a, b) => b.best - a.best).slice(0, 3);
  const matched = job.skills.filter((s) => CV_SKILLS.has(s));
  const missing = job.skills.filter((s) => !CV_SKILLS.has(s) && !SOFT_SKILLS.has(s));
  const technical = matched.filter((s) => !SOFT_SKILLS.has(s));
  const top = (technical.length ? technical : matched).slice(0, 3);
  const fallbackSkills = CV.skills.flatMap((g) => g.items).slice(0, 3);
  const isMatch = (item) => findSkills(item).some((s) => jobSkills.has(s)) || job.skills.some((s) => fold(item).includes(fold(s)));
  const skills = CV.skills.map((g, gi) => {
    const items = g.items.map((x, i) => ({ x, i, m: isMatch(x) })).sort((a, b) => (b.m - a.m) || a.i - b.i);
    return { label: g.label, items: items.map((o) => o.x).join(", "), m: items.filter((o) => o.m).length, gi };
  }).sort((a, b) => b.m - a.m || a.gi - b.gi).map(({ label, items }) => ({ label, items }));
  const eduFirst = /scien|graduate|trainee|grad\b/i.test(job.title) || /scien/i.test(job.family);
  const cv = {
    headline: `${cleanJobTitle(job.title)}  ·  ${humanJoin(top.length ? top : fallbackSkills)}`,
    profile: CV.profile,
    order: eduFirst ? ["profile", "education", "skills", "experience", "projects", "certifications", "languages"]
      : ["profile", "skills", "experience", "projects", "education", "certifications", "languages"],
    skills,
    experience: exp.map(({ e, r }) => ({ title: e.title, org: e.org, place: e.place, dates: e.dates, bullets: r.slice(0, 6).map((o) => o.b) })),
    projects: proj.map(({ p, r }) => ({ name: p.name, dates: p.dates, bullets: r.slice(0, 4).map((o) => o.b) })),
    education: edu.map(({ e, r }) => ({ degree: e.degree, school: e.school, place: e.place, dates: e.dates, bullets: r.slice(0, 4).map((o) => o.b) })),
    certifications: CV.certifications.join("  ·  "),
    languages: CV.languages.join("  ·  "),
  };
  // Letter
  const best = [...exp].sort((a, b) => b.best - a.best);
  const j1 = best[0], j2 = best[1];
  const e0 = CV.education[0];
  const school = e0 ? str(e0.school) : "";
  const vals = {
    role: job.title, company: job.company || "your organisation", location: job.location,
    degree: e0 && e0.degree ? `${article(e0.degree)} ${e0.degree}${school ? ` from ${/^university of/i.test(school) ? "the " : ""}${school}` : ""}` : "",
    skills: humanJoin(top.length ? top : fallbackSkills), skills_short: humanJoin((top.length ? top : fallbackSkills).slice(0, 2)),
    job1_title: j1 && j1.e.title ? `${article(j1.e.title)} ${j1.e.title}` : "", job1_org: j1 ? j1.e.org : "",
    job1_point: j1 && j1.r[0] ? clause(j1.r[0].b) : "", job1_point2: j1 && j1.r[1] ? clause(j1.r[1].b) : "",
    job2_org: j2 ? j2.e.org : "", job2_point: j2 && j2.r[0] ? clause(j2.r[0].b) : "",
    relocation: "", sponsorship: S.settings.workStatus === "sponsorship" ? "I would require Skilled Worker visa sponsorship for this role."
      : "I currently have permission to work in the UK and would require Skilled Worker visa sponsorship for this role going forward.",
  };
  const home = fold(S.details.home);
  const loc = fold(job.location);
  if (S.settings.relocate && loc && !/remote/.test(loc) && !(home && loc.includes(home))) vals.relocation = `I am happy to relocate to ${job.location.split(",")[0]}. `;
  const letter = { date: fmtDate(today(), true), to: ["Hiring Manager", job.company].filter(Boolean), greeting: "Dear Hiring Manager,", paragraphs: renderTemplate(S.settings.template || DEFAULT_TEMPLATE, vals), signoff: "Yours faithfully," };
  return { cv, letter, matched, missing };
}
function renderTemplate(tpl, vals) {
  const optional = new Set(["relocation"]);
  const out = [];
  for (const para of str(tpl).split(/\n\s*\n/)) {
    const sentences = para.trim().split(/(?<=[.!?])\s+(?=\S)/);
    const kept = [];
    for (const s of sentences) {
      let dropped = false;
      const filled = s.replace(/\{(\w+)\}/g, (m, k) => {
        if (!(k in vals)) return m;
        const v = str(vals[k]);
        if (!v && !optional.has(k)) dropped = true;
        return v;
      });
      if (!dropped && filled.trim()) kept.push(filled.trim());
    }
    if (kept.length) out.push(kept.join(" ").replace(/\s{2,}/g, " "));
  }
  return out;
}

// ------------------------------------------------------------------ documents dialog
let openId = null, docTab = "cv", current = null;
function docsFor(id) {
  const saved = S.docs[id];
  if (saved && obj(saved).cv) return { ...saved, edited: true };
  const it = itemFor(id);
  if (!CV || !it) return null;
  const job = BY_ID.get(id) || { ...it, skills: arr(it.skills), desc: str(it.desc), title: str(it.title) };
  return { ...tailor(job), edited: false };
}
function openDocs(id) {
  const it = itemFor(id); if (!it) return;
  openId = id; docTab = "cv";
  current = docsFor(id);
  renderDocs();
  const d = $("dlg"); if (!d.open) { openDocs.lastFocus = document.activeElement; d.showModal(); }
}
function closeDocs() { const d = $("dlg"); if (d.open) d.close(); }
$("dlgClose").addEventListener("click", closeDocs);
$("dlg").addEventListener("close", () => { openId = null; if (openDocs.lastFocus && openDocs.lastFocus.focus) openDocs.lastFocus.focus(); });
$("dlg").addEventListener("click", (e) => { if (e.target === $("dlg")) closeDocs(); });

function renderDocs() {
  const it = itemFor(openId); if (!it) return;
  $("dlgTitle").textContent = it.title;
  $("dlgSub").textContent = [it.company, it.location].filter(Boolean).join(" · ");
  const body = $("dlgBody");
  if (!current) {
    body.replaceChildren(el("div", { class: "panel" }, el("p", null, "Add your CV first: the CV and cover letter are built from your own CV bullet points."),
      el("div", { class: "row" }, el("a", { class: "btn primary", href: "#cv", onclick: closeDocs }, "Add my CV"))));
    return;
  }
  const actions = el("div", { class: "row" },
    el("button", { class: "primary", type: "button", onclick: () => downloadPdf("cv") }, "Download CV (PDF)"),
    el("button", { type: "button", onclick: () => downloadPdf("letter") }, "Download cover letter (PDF)"),
    el("button", { type: "button", onclick: copyText }, "Copy as text"),
    current.edited ? el("button", { type: "button", class: "quiet", onclick: rebuild }, "Rebuild from my CV") : null,
    el("span", { class: "status-msg", id: "docMsg", role: "status" }));
  const tabs = el("div", { class: "tabs", role: "tablist" }, [["cv", "CV"], ["letter", "Cover letter"]].map(([k, l]) =>
    el("button", { type: "button", role: "tab", "aria-selected": String(docTab === k), onclick: () => { docTab = k; renderDocs(); } }, l)));
  const notes = [];
  if (current.matched && current.matched.length) notes.push(el("p", { class: "small" }, el("strong", null, "Matches the advert on: "), current.matched.join(", ")));
  if (current.missing && current.missing.length) notes.push(el("p", { class: "small muted" }, el("strong", null, "The advert also mentions: "), current.missing.join(", "), ". They aren't in your CV, so they're left out."));
  notes.push(el("p", { class: "small muted" }, current.edited ? "You've edited this version. Click any text to edit it again." : "Built from your CV. Click any text to edit it; your edits are kept for this role."));
  body.replaceChildren(actions, tabs, ...notes, el("div", { class: "sheet-wrap" }, docTab === "cv" ? cvSheet(current.cv) : letterSheet(current.letter)));
}
function rebuild() { delete S.docs[openId]; save(); current = docsFor(openId); renderDocs(); toast("Rebuilt from your CV"); }
function ed(path, text, tag = "span", cls) {
  const n = el(tag, { class: cls, contenteditable: "true", spellcheck: "true", "data-path": path, role: "textbox", "aria-label": "Edit " + path.split(".").pop() }, text);
  n.addEventListener("blur", () => saveEdit(path, n.innerText.replace(/\s+\n/g, "\n").trim()));
  n.addEventListener("keydown", (e) => { if (e.key === "Enter" && tag !== "p" && tag !== "div") { e.preventDefault(); n.blur(); } });
  return n;
}
function contactLine() {
  const d = S.details;
  return [str(d.email), str(d.phone), str(d.linkedin), str(d.home)].map((s) => s.trim()).filter(Boolean).join("  ·  ");
}
const cvSections = (cv) => {
  const def = ["profile", "skills", "experience", "projects", "education", "certifications", "languages"];
  const order = arr(cv.order).map(str).filter((s) => def.includes(s));
  for (const s of def) if (!order.includes(s)) order.push(s);
  return order;
};
const listText = (v) => (Array.isArray(v) ? v.map(str).filter(Boolean).join("  ·  ") : str(v));
function cvSheet(cv) {
  const sheet = el("div", { class: "sheet" },
    el("div", { class: "nm" }, str(S.details.name) || "Your name"),
    el("div", { class: "ct" }, contactLine() || "Add your contact details under My details"),
    ed("cv.headline", str(cv.headline), "div", "hl"));
  for (const s of cvSections(cv)) {
    if (s === "profile" && str(cv.profile)) sheet.append(el("div", { class: "sh" }, "Profile"), ed("cv.profile", str(cv.profile), "p"));
    if (s === "skills" && arr(cv.skills).length) sheet.append(el("div", { class: "sh" }, "Skills"), el("div", { class: "ent" }, arr(cv.skills).map((g, i) =>
      el("div", null, el("b", null, ed(`cv.skills.${i}.label`, str(obj(g).label))), ": ", ed(`cv.skills.${i}.items`, listText(obj(g).items))))));
    if (s === "experience" && arr(cv.experience).length) sheet.append(el("div", { class: "sh" }, "Experience"), ...arr(cv.experience).map((x, i) => entry("experience", i, obj(x), "title", "org")));
    if (s === "projects" && arr(cv.projects).length) sheet.append(el("div", { class: "sh" }, "Projects"), ...arr(cv.projects).map((x, i) => entry("projects", i, obj(x), "name", null)));
    if (s === "education" && arr(cv.education).length) sheet.append(el("div", { class: "sh" }, "Education"), ...arr(cv.education).map((x, i) => entry("education", i, obj(x), "degree", "school")));
    if (s === "certifications" && listText(cv.certifications)) sheet.append(el("div", { class: "sh" }, "Certifications"), ed("cv.certifications", listText(cv.certifications), "p"));
    if (s === "languages" && listText(cv.languages)) sheet.append(el("div", { class: "sh" }, "Languages"), ed("cv.languages", listText(cv.languages), "p"));
  }
  return sheet;
}
function entry(sec, i, x, titleKey, orgKey) {
  const base = `cv.${sec}.${i}`;
  return el("div", { class: "ent" },
    el("div", { class: "eh" }, ed(`${base}.${titleKey}`, str(x[titleKey])), ed(`${base}.dates`, str(x.dates))),
    orgKey ? el("div", { class: "eo" }, ed(`${base}.${orgKey}`, str(x[orgKey])), str(x.place) ? ", " : "", str(x.place) ? ed(`${base}.place`, str(x.place)) : null) : null,
    arr(x.bullets).length ? el("ul", null, arr(x.bullets).map((b, j) => el("li", null, ed(`${base}.bullets.${j}`, str(b))))) : null);
}
function letterSheet(l) {
  return el("div", { class: "sheet lt" },
    el("div", { class: "nm" }, str(S.details.name) || "Your name"),
    el("div", { class: "ct" }, contactLine() || "Add your contact details under My details"),
    ed("letter.date", str(l.date), "div"),
    el("div", null, arr(l.to).map((t, i) => el("div", null, ed(`letter.to.${i}`, str(t))))),
    ed("letter.greeting", str(l.greeting) || "Dear Hiring Manager,", "div"),
    arr(l.paragraphs).map((t, i) => ed(`letter.paragraphs.${i}`, str(t), "p")),
    ed("letter.signoff", str(l.signoff) || "Yours faithfully,", "div"),
    el("div", { style: "font-weight:700" }, str(S.details.name)));
}
function saveEdit(path, value) {
  if (!current || !openId) return;
  const parts = path.split("."), root = parts[0];
  const clone = JSON.parse(JSON.stringify(obj(current[root])));
  let cur = clone;
  for (let i = 1; i < parts.length - 1; i++) {
    const k = /^\d+$/.test(parts[i]) ? Number(parts[i]) : parts[i];
    if (cur[k] == null || typeof cur[k] !== "object") cur[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
    cur = cur[k];
  }
  const last = parts[parts.length - 1], k = /^\d+$/.test(last) ? Number(last) : last;
  const old = cur[k];
  if ((Array.isArray(old) ? listText(old) : str(old)) === value) return;
  cur[k] = value;
  if (Array.isArray(cur) && !value) cur.splice(k, 1);
  const wasEdited = current.edited;
  current = { ...current, [root]: clone, edited: true };
  S.docs[openId] = { cv: current.cv, letter: current.letter, matched: current.matched, missing: current.missing, editedOn: new Date().toISOString() };
  const it = itemFor(openId);
  if (it && !it._gone) S.snapshots[openId] = snapshotOf(it);
  if (!save()) return;
  if (!wasEdited) {
    // Show "Rebuild from my CV" now, keeping the cursor where the user moved it.
    setTimeout(() => {
      const path = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.path : null;
      renderDocs();
      if (path) { const n = document.querySelector(`[data-path="${path}"]`); if (n) n.focus(); }
      setMsg("docMsg", "Saved", "ok");
    }, 0);
  } else setMsg("docMsg", "Saved", "ok");
}

// ------------------------------------------------------------------ PDF
function loadScript(src, globalName) {
  if (globalName && window[globalName]) return Promise.resolve(window[globalName]);
  loadScript.p = loadScript.p || {};
  if (!loadScript.p[src]) loadScript.p[src] = new Promise((res, rej) => {
    const s = document.createElement("script"); s.src = src; s.async = true;
    s.onload = () => res(globalName ? window[globalName] : true);
    s.onerror = () => { delete loadScript.p[src]; rej(new Error("Couldn't load " + src)); };
    document.head.append(s);
  });
  return loadScript.p[src];
}
const WIN = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ";
function pdfSafe(s) {
  return str(s).replace(/[‐‑‒]/g, "-").replace(/ /g, " ").split("").map((ch) => {
    if (ch.charCodeAt(0) < 256 || WIN.includes(ch)) return ch;
    const base = ch.normalize("NFKD").replace(/[̀-ͯ]/g, "");
    return base && base.charCodeAt(0) < 256 ? base : "";
  }).join("");
}
const fileSafe = (s) => str(s).replace(/[\\/:*?"<>|]+/g, "").replace(/\s+/g, " ").trim().slice(0, 80) || "Untitled";
async function buildPdf(kind, docs, details, company) {
  const lib = await loadScript(VENDOR.jspdf, "jspdf");
  const jsPDF = lib.jsPDF;
  const W = 210, H = 297, M = 16, CW = W - 2 * M;
  const name = pdfSafe(str(details.name) || "Your name");
  const contact = pdfSafe([str(details.email), str(details.phone), str(details.linkedin), str(details.home)].map((s) => s.trim()).filter(Boolean).join("  ·  "));
  const render = (scale, allowBreak) => {
    const doc = new jsPDF({ unit: "mm", format: "a4", compress: true });
    doc.setProperties({ title: kind === "cv" ? `${name} CV` : `${name} Cover letter`, author: name });
    let y = M, overflow = false;
    const lh = (size) => size * 0.3528 * 1.32;
    const need = (h) => { if (y + h > H - M) { if (allowBreak) { doc.addPage(); y = M; } else overflow = true; } };
    const ink = [27, 27, 31];
    const text = (t, size, style = "normal", indent = 0, after = 0, color = ink) => {
      doc.setFont("helvetica", style); doc.setFontSize(size); doc.setTextColor(...color);
      for (const ln of doc.splitTextToSize(pdfSafe(t), CW - indent)) { need(lh(size)); y += lh(size) * 0.78; doc.text(ln, M + indent, y); y += lh(size) * 0.22; }
      y += after;
    };
    const s = (n) => n * scale;
    text(name, s(19), "bold", 0, s(0.6));
    if (contact) text(contact, s(9.2), "normal", 0, s(2.4), [70, 70, 82]);
    if (kind === "cv") {
      const cv = obj(docs.cv);
      if (str(cv.headline)) text(cv.headline, s(11), "bold", 0, s(1.6));
      const heading = (t) => { need(lh(s(10)) + 4); y += s(2.2); doc.setFont("helvetica", "bold"); doc.setFontSize(s(9.6)); doc.setTextColor(13, 79, 73); y += lh(s(9.6)) * 0.78; doc.text(t.toUpperCase(), M, y, { charSpace: 0.35 }); y += s(1.4); doc.setDrawColor(190, 200, 198); doc.setLineWidth(0.25); doc.line(M, y, W - M, y); y += s(2.2); };
      const bullets = (list) => { for (const b of arr(list).map(str).filter(Boolean)) { doc.setFont("helvetica", "normal"); doc.setFontSize(s(10)); doc.setTextColor(...ink); const lines = doc.splitTextToSize(pdfSafe(b), CW - 5); need(lh(s(10)) * Math.min(lines.length, 2)); lines.forEach((ln, i) => { need(lh(s(10))); y += lh(s(10)) * 0.78; if (i === 0) doc.text("•", M + 1.2, y); doc.text(ln, M + 5, y); y += lh(s(10)) * 0.22; }); } };
      const ent = (title, right, sub, list) => {
        need(lh(s(10.4)) * 3);
        doc.setFont("helvetica", "bold"); doc.setFontSize(s(10.4)); doc.setTextColor(...ink);
        const rightW = right ? doc.getTextWidth(pdfSafe(right)) + 4 : 0;
        doc.splitTextToSize(pdfSafe(title), CW - rightW).forEach((ln, i) => { y += lh(s(10.4)) * 0.78; doc.text(ln, M, y); if (i === 0 && right) { doc.setFont("helvetica", "normal"); doc.setFontSize(s(9.6)); doc.text(pdfSafe(right), W - M, y, { align: "right" }); doc.setFont("helvetica", "bold"); doc.setFontSize(s(10.4)); } y += lh(s(10.4)) * 0.22; });
        if (sub) text(sub, s(9.8), "italic", 0, 0, [70, 70, 82]);
        bullets(list); y += s(1.6);
      };
      for (const sec of cvSections(cv)) {
        if (sec === "profile" && str(cv.profile)) { heading("Profile"); text(cv.profile, s(10), "normal", 0, s(0.8)); }
        if (sec === "skills" && arr(cv.skills).length) {
          heading("Skills"); doc.setTextColor(...ink);
          for (const g of arr(cv.skills).map(obj)) {
            const lbl = str(g.label), items = listText(g.items); if (!lbl && !items) continue;
            doc.setFontSize(s(10));
            doc.splitTextToSize(pdfSafe((lbl ? lbl + ": " : "") + items), CW - 4).forEach((ln, i) => {
              need(lh(s(10))); y += lh(s(10)) * 0.78;
              if (i === 0 && lbl) { doc.setFont("helvetica", "bold"); const lw = doc.getTextWidth(pdfSafe(lbl + ": ")); doc.text(pdfSafe(lbl + ":"), M, y); doc.setFont("helvetica", "normal"); doc.text(ln.slice(pdfSafe(lbl + ": ").length), M + lw, y); }
              else { doc.setFont("helvetica", "normal"); doc.text(ln, M, y); }
              y += lh(s(10)) * 0.22;
            });
            y += s(0.6);
          }
        }
        if (sec === "experience" && arr(cv.experience).length) { heading("Experience"); for (const x of arr(cv.experience).map(obj)) ent(str(x.title), str(x.dates), [str(x.org), str(x.place)].filter(Boolean).join(", "), x.bullets); }
        if (sec === "projects" && arr(cv.projects).length) { heading("Projects"); for (const x of arr(cv.projects).map(obj)) ent(str(x.name), str(x.dates), "", x.bullets); }
        if (sec === "education" && arr(cv.education).length) { heading("Education"); for (const x of arr(cv.education).map(obj)) ent(str(x.degree), str(x.dates), [str(x.school), str(x.place)].filter(Boolean).join(", "), x.bullets); }
        if (sec === "certifications" && listText(cv.certifications)) { heading("Certifications"); text(listText(cv.certifications), s(10)); }
        if (sec === "languages" && listText(cv.languages)) { heading("Languages"); text(listText(cv.languages), s(10)); }
      }
    } else {
      const l = obj(docs.letter);
      y += s(2);
      if (str(l.date)) text(l.date, s(10.5), "normal", 0, s(3));
      for (const t of arr(l.to).map(str).filter(Boolean)) text(t, s(10.5));
      y += s(3);
      text(str(l.greeting) || "Dear Hiring Manager,", s(10.5), "normal", 0, s(2.6));
      for (const p of arr(l.paragraphs).map(str).filter(Boolean)) text(p, s(10.5), "normal", 0, s(2.6));
      y += s(1);
      text(str(l.signoff) || "Yours faithfully,", s(10.5), "normal", 0, s(7));
      text(name, s(10.5), "bold");
    }
    return { doc, overflow };
  };
  let out = null;
  for (let sc = 1; sc >= 0.8; sc -= 0.04) { out = render(sc, false); if (!out.overflow) break; }
  if (out.overflow) out = render(0.88, true);
  const filename = `${fileSafe(str(details.name) || "My")} ${kind === "cv" ? "CV" : "Cover letter"} - ${fileSafe(company)}.pdf`;
  return { filename, blob: out.doc.output("blob"), pages: out.doc.getNumberOfPages() };
}
function saveFile(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename, style: "display:none" });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
async function downloadPdf(kind) {
  if (!current) return;
  setMsg("docMsg", "Preparing the PDF…");
  try {
    const it = itemFor(openId);
    const { filename, blob } = await buildPdf(kind, current, S.details, it ? it.company : "");
    saveFile(filename, blob);
    setMsg("docMsg", "Downloaded", "ok");
  } catch (e) { setMsg("docMsg", "Couldn't make the PDF: " + ((e && e.message) || "unknown error"), "err"); }
}
function docsAsText(docs) {
  const cv = obj(docs.cv), l = obj(docs.letter);
  const out = [str(S.details.name), contactLine(), "", str(cv.headline)];
  for (const s of cvSections(cv)) {
    if (s === "profile" && str(cv.profile)) out.push("", "PROFILE", str(cv.profile));
    if (s === "skills" && arr(cv.skills).length) { out.push("", "SKILLS"); arr(cv.skills).map(obj).forEach((g) => out.push(`${str(g.label)}: ${listText(g.items)}`)); }
    const ents = (title, list, t, o) => { if (!arr(list).length) return; out.push("", title); arr(list).map(obj).forEach((x) => { out.push([str(x[t]), str(x.dates)].filter(Boolean).join(" | ")); if (o) out.push([str(x[o]), str(x.place)].filter(Boolean).join(", ")); arr(x.bullets).forEach((b) => out.push("• " + str(b))); }); };
    if (s === "experience") ents("EXPERIENCE", cv.experience, "title", "org");
    if (s === "projects") ents("PROJECTS", cv.projects, "name", null);
    if (s === "education") ents("EDUCATION", cv.education, "degree", "school");
    if (s === "certifications" && listText(cv.certifications)) out.push("", "CERTIFICATIONS", listText(cv.certifications));
    if (s === "languages" && listText(cv.languages)) out.push("", "LANGUAGES", listText(cv.languages));
  }
  out.push("", "", "COVER LETTER", "", str(l.date), ...arr(l.to).map(str), "", str(l.greeting) || "Dear Hiring Manager,", "", ...arr(l.paragraphs).map(str).flatMap((x) => [x, ""]), str(l.signoff) || "Yours faithfully,", str(S.details.name));
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
async function copyText() {
  const t = docsAsText(current || {});
  try { await navigator.clipboard.writeText(t); setMsg("docMsg", "Copied", "ok"); }
  catch {
    const ta = el("textarea", { rows: "10", "aria-label": "CV and cover letter as text", readonly: true }); ta.value = t;
    $("dlgBody").append(ta); ta.focus(); ta.select(); setMsg("docMsg", "Copy the selected text below.");
  }
}

// ------------------------------------------------------------------ CV upload and forms
async function pdfText(buf) {
  await loadScript(VENDOR.pdfWorker, "pdfjsWorker");
  const lib = await loadScript(VENDOR.pdf, "pdfjsLib");
  lib.GlobalWorkerOptions.workerSrc = VENDOR.pdfWorker;
  const pdf = await lib.getDocument({ data: new Uint8Array(buf), isEvalSupported: false, useSystemFonts: true }).promise;
  const pages = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const tc = await (await pdf.getPage(i)).getTextContent();
    let lastY = null, lastEnd = null, line = "";
    const lines = [];
    for (const it of tc.items) {
      const t = it.transform || [1, 0, 0, 1, 0, lastY || 0];
      const y = Math.round(t[5]), x = t[4], h = Math.abs(t[3]) || 10;
      if (lastY !== null && Math.abs(y - lastY) > 2) { lines.push(line); line = ""; lastEnd = null; }
      if (line && lastEnd !== null && x - lastEnd > h * 0.15 && !line.endsWith(" ") && !String(it.str).startsWith(" ")) line += " ";
      line += it.str || "";
      lastEnd = x + (it.width || 0); lastY = y;
      if (it.hasEOL) { lines.push(line); line = ""; lastEnd = null; }
    }
    if (line) lines.push(line);
    pages.push(lines.map((l) => l.replace(/\s{2,}/g, " ").trim()).join("\n"));
  }
  return pages.join("\n\n");
}
async function docxText(buf) {
  const JSZip = await loadScript(VENDOR.jszip, "JSZip");
  const zip = await JSZip.loadAsync(buf);
  const file = zip.file("word/document.xml");
  if (!file) throw new Error("That Word file has no document text");
  const xml = await file.async("string");
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
  return [...doc.getElementsByTagNameNS(W, "p")].map((p) => {
    const bullet = p.getElementsByTagNameNS(W, "numPr").length ? "• " : "";
    let t = "";
    for (const n of p.getElementsByTagNameNS("*", "*")) {
      if (n.namespaceURI !== W) continue;
      if (n.localName === "t") t += n.textContent;
      else if (n.localName === "tab") t += " ";
      else if (n.localName === "br") t += "\n";
    }
    return t.trim() ? bullet + t : "";
  }).join("\n");
}
async function onCvFile(e) {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  const msg = (t, err) => { $("cvFileMsg").textContent = t; $("cvFileMsg").style.color = err ? "var(--bad)" : ""; };
  msg(`Reading ${f.name}…`);
  try {
    const name = f.name.toLowerCase();
    let text;
    if (name.endsWith(".pdf") || f.type === "application/pdf") text = await pdfText(await f.arrayBuffer());
    else if (name.endsWith(".docx")) text = await docxText(await f.arrayBuffer());
    else if (name.endsWith(".doc")) throw new Error("Old .doc files can't be read. Save it as .docx or PDF, or paste the text");
    else text = await f.text();
    text = text.replace(/\r/g, "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    if (!text) throw new Error("No text found. If it's a scanned PDF, paste the text instead");
    $("cvRaw").value = text;
    makeOutline();
    msg(`Read ${f.name}. Check the outline, then save.`);
  } catch (err) { msg(((err && err.message) || "Couldn't read that file") + ".", true); }
  e.target.value = "";
}
function makeOutline() {
  const raw = $("cvRaw").value.trim();
  if (!raw) { setMsg("outlineMsg", "Paste or upload your CV first.", "err"); return; }
  const { outline, details } = rawToOutline(raw);
  $("cvOutline").value = outline;
  const filled = [];
  for (const [k, id] of [["name", "d_name"], ["email", "d_email"], ["phone", "d_phone"], ["linkedin", "d_linkedin"], ["home", "d_home"]]) {
    if (details[k] && !$(id).value.trim()) { $(id).value = details[k]; filled.push(k); }
  }
  const cv = parseOutline(outline);
  setMsg("outlineMsg", `Found ${cv.experience.length} job${cv.experience.length === 1 ? "" : "s"}, ${cv.education.length} qualification${cv.education.length === 1 ? "" : "s"} and ${cv.skills.reduce((a, g) => a + g.items.length, 0)} skills.` +
    (filled.length ? " Your details were filled in below: check and save them." : " Check it, then save."), "ok");
}
function saveCv() {
  const outline = $("cvOutline").value;
  const cv = parseOutline(outline);
  if (!outlineHasContent(cv)) { setMsg("cvMsg", "The outline is empty. Turn your CV into an outline first, or follow the guide.", "err"); return; }
  S.cvRaw = $("cvRaw").value; S.cvOutline = outline; S.cvSavedAt = new Date().toISOString();
  if (save()) { setMsg("cvMsg", "Saved. Roles are now ranked by fit with your CV.", "ok"); rankAll(); renderAll(); renderCvPreview(); }
}
function wsSentence() {
  return $("ws_sponsorship").checked ? "I would require Skilled Worker visa sponsorship for this role."
    : "I currently have permission to work in the UK and would require Skilled Worker visa sponsorship for this role going forward.";
}
function fillDetails() {
  const d = S.details;
  $("d_name").value = str(d.name); $("d_email").value = str(d.email); $("d_phone").value = str(d.phone); $("d_linkedin").value = str(d.linkedin); $("d_home").value = str(d.home);
  $(S.settings.workStatus === "sponsorship" ? "ws_sponsorship" : "ws_permission").checked = true;
  $("d_relocate").checked = S.settings.relocate !== false;
  $("d_template").value = S.settings.template || DEFAULT_TEMPLATE;
  $("wsSentence").textContent = wsSentence();
}
function saveDetails(e) {
  e.preventDefault();
  if (!$("d_name").value.trim()) { setMsg("detailsMsg", "Add your full name.", "err"); return; }
  S.details = { name: $("d_name").value.trim(), email: $("d_email").value.trim(), phone: $("d_phone").value.trim(), linkedin: $("d_linkedin").value.trim(), home: $("d_home").value.trim() };
  S.settings.workStatus = $("ws_sponsorship").checked ? "sponsorship" : "permission";
  S.settings.relocate = $("d_relocate").checked;
  const tpl = $("d_template").value.trim();
  S.settings.template = tpl && tpl !== DEFAULT_TEMPLATE ? tpl : "";
  if (save()) { setMsg("detailsMsg", "Saved", "ok"); renderStart(); }
}
function backup() {
  const blob = new Blob([JSON.stringify(S, null, 1)], { type: "application/json" });
  saveFile(`sponsor-radar-backup-${today()}.json`, blob);
  setMsg("dataMsg", "Backup downloaded. Keep it somewhere private: it contains your CV.", "ok");
}
async function restore(e) {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    if (!data || data.v !== 1 || typeof data.statuses !== "object") throw new Error("not a Sponsor Radar backup");
    S = normalizeStore(data);
    if (save()) { loadCv(); fillDetails(); $("cvRaw").value = S.cvRaw; $("cvOutline").value = S.cvOutline; rankAll(); renderAll(); renderCvPreview(); setMsg("dataMsg", "Restored from the backup.", "ok"); }
  } catch (err) { setMsg("dataMsg", "That file couldn't be restored: " + ((err && err.message) || "unreadable"), "err"); }
  e.target.value = "";
}
function clearAll() {
  S = blankStore();
  try { localStorage.removeItem(STORE_KEY); } catch {}
  loadCv(); fillDetails(); $("cvRaw").value = ""; $("cvOutline").value = "";
  rankAll(); renderAll(); renderCvPreview();
  $("clearConfirm").hidden = true; setMsg("dataMsg", "Everything was deleted from this browser.", "ok");
}

// ------------------------------------------------------------------ search settings (saved to GitHub)
const GH_KEY = "sponsorRadar.github";
const GOING_RATES = "https://www.gov.uk/government/publications/skilled-worker-visa-going-rates-for-eligible-occupations";
const NEW_ENTRANT_FLOOR = 33400;
const MAX_SEARCHES = 30;
const gh = {
  get token() { try { return localStorage.getItem(GH_KEY) || ""; } catch { return ""; } },
  set(t) { try { if (t) localStorage.setItem(GH_KEY, t); else localStorage.removeItem(GH_KEY); } catch {} },
};
let SEARCH = null;        // settings the last search used (data/settings.json)
const list = (v) => arr(v).map(str).map((x) => x.trim()).filter(Boolean);
const csv = (v) => str(v).split(",").map((x) => x.trim()).filter(Boolean);
function currentSettings() {
  const saved = obj(S.searchSaved);
  const savedAt = Date.parse(saved.at || ""), ranAt = Date.parse(META.generated_at || "");
  if (saved.settings && !isNaN(savedAt) && (isNaN(ranAt) || savedAt > ranAt)) return { ...saved.settings, _pending: saved.at };
  if (SEARCH) return SEARCH;
  return { targets: arr(META.targets).map((t) => ({ name: str(obj(t).name), searches: list(obj(t).searches), min_salary: num(obj(t).min_salary) || 0 })),
    max_years_experience: 2, locations: [], include_remote: true, exclude_title_words: [], reject_if_required: [], quiet_hours: { from: "22:30", to: "07:00" } };
}
function targetCard(t, i) {
  const id = (k) => `t${i}_${k}`;
  return el("div", { class: "target", "data-i": String(i) },
    el("div", { class: "target-head" }, el("strong", null, `Role type ${i + 1}`),
      el("button", { type: "button", class: "quiet", onclick: () => { const all = readTargets(); all.splice(i, 1); drawTargets(all); } }, "Remove")),
    el("label", { for: id("name") }, "Name", el("input", { id: id("name"), "data-k": "name", value: str(t.name), placeholder: "e.g. Data analyst", autocomplete: "off" })),
    el("label", { for: id("searches") }, "Job titles to search for", el("span", { class: "help" }, "Separate with commas. Each title is searched on Reed."),
      (() => { const ta = el("textarea", { id: id("searches"), "data-k": "searches", rows: "2", placeholder: "e.g. data analyst, bi analyst", autocomplete: "off" }); ta.value = list(t.searches).join(", "); return ta; })()),
    el("label", { for: id("min") }, "Lowest salary that can be sponsored (£)",
      el("span", { class: "help" }, "Roles advertised below this are skipped. ", el("a", { href: GOING_RATES, target: "_blank", rel: "noopener" }, "Check the going rate on gov.uk")),
      el("input", { id: id("min"), "data-k": "min", type: "number", min: "0", step: "100", inputmode: "numeric", value: String(num(t.min_salary) ?? NEW_ENTRANT_FLOOR) })));
}
function readTargets() {
  return [...$("targetsEditor").querySelectorAll(".target")].map((c) => ({
    name: c.querySelector("[data-k=name]").value.trim(),
    searches: csv(c.querySelector("[data-k=searches]").value).map((x) => x.toLowerCase()),
    min_salary: parseInt(c.querySelector("[data-k=min]").value, 10),
  }));
}
function drawTargets(targets) {
  $("targetsEditor").replaceChildren(...targets.map(targetCard));
  $("addTargetBtn").disabled = targets.length >= 8;
  countSearches();
}
function countSearches() {
  const n = readTargets().reduce((a, t) => a + t.searches.length, 0);
  $("searchCount").textContent = `${n} job title${n === 1 ? "" : "s"} searched (up to ${MAX_SEARCHES})`;
}
function fillSearchForm() {
  const st = currentSettings();
  drawTargets(arr(st.targets).length ? arr(st.targets) : [{ name: "", searches: [], min_salary: NEW_ENTRANT_FLOOR }]);
  $("s_years").value = num(st.max_years_experience) ?? 2;
  const places = list(st.locations);
  $("s_anywhere").checked = !places.length;
  $("s_places").value = places.join(", ");
  $("s_places").disabled = !places.length;
  $("s_remote").checked = st.include_remote !== false;
  $("s_exclude").value = list(st.exclude_title_words).join(", ");
  $("s_require").value = list(st.reject_if_required).join(", ");
  $("s_qfrom").value = /^\d{2}:\d{2}$/.test(str(obj(st.quiet_hours).from)) ? st.quiet_hours.from : "22:30";
  $("s_qto").value = /^\d{2}:\d{2}$/.test(str(obj(st.quiet_hours).to)) ? st.quiet_hours.to : "07:00";
  $("searchSource").textContent = st._pending ? `Saved ${fmtStamp(st._pending)}: used from the next search`
    : (SEARCH && SEARCH.source === "settings.json") ? "Your saved settings" : "Default settings";
}
function readSearchForm() {
  const targets = readTargets().filter((t) => t.name || t.searches.length);
  if (!targets.length) return { error: "Add at least one role type with a name and a job title." };
  for (const [i, t] of targets.entries()) {
    if (!t.name) return { error: `Give role type ${i + 1} a name.` };
    if (!t.searches.length) return { error: `Add at least one job title to search for in "${t.name}".` };
    if (!Number.isFinite(t.min_salary) || t.min_salary < 0 || t.min_salary > 250000) return { error: `Check the salary for "${t.name}".` };
  }
  const total = targets.reduce((a, t) => a + t.searches.length, 0);
  if (total > MAX_SEARCHES) return { error: `That's ${total} job titles. Keep it to ${MAX_SEARCHES} or fewer so the searches stay within Reed's limits.` };
  const years = parseInt($("s_years").value, 10);
  if (!Number.isFinite(years) || years < 0 || years > 15) return { error: "Set the years of experience between 0 and 15." };
  const anywhere = $("s_anywhere").checked;
  const places = anywhere ? [] : csv($("s_places").value);
  if (!anywhere && !places.length) return { error: "Add at least one town or city, or tick Anywhere in the UK." };
  return { settings: {
    targets: targets.map((t) => ({ name: t.name, searches: t.searches, min_salary: t.min_salary })),
    max_years_experience: years, locations: places, include_remote: $("s_remote").checked,
    exclude_title_words: csv($("s_exclude").value).map((x) => x.toLowerCase()),
    reject_if_required: csv($("s_require").value).map((x) => x.toLowerCase()),
    quiet_hours: { from: $("s_qfrom").value || "22:30", to: $("s_qto").value || "07:00" },
  } };
}
async function ghApi(method, path, body) {
  let r;
  try {
    r = await fetch("https://api.github.com" + path, { method, cache: "no-store", headers: {
      Accept: "application/vnd.github+json", Authorization: "Bearer " + gh.token, "X-GitHub-Api-Version": "2022-11-28",
      ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
  } catch { return { status: 0, data: null }; }
  let data = null; try { data = await r.json(); } catch {}
  return { status: r.status, data };
}
function b64(text) { let bin = ""; for (const b of new TextEncoder().encode(text)) bin += String.fromCharCode(b); return btoa(bin); }
function ghError(status) {
  if (status === 0) return "Couldn't reach GitHub. Check your connection and try again.";
  if (status === 401) return "GitHub didn't accept the key: it may have expired or been deleted. Create a new one and connect again.";
  if (status === 403 || status === 404) return `The key can't save to ${META.repo}. Check it has access to this repository with Contents set to Read and write.`;
  return `GitHub answered with an error (${status}). Try again in a minute.`;
}
async function saveSearch(e) {
  e.preventDefault();
  const { settings, error } = readSearchForm();
  if (error) { setMsg("searchMsg", error, "err"); return; }
  if (!META.repo) { setMsg("searchMsg", "This page doesn't know its repository yet. Wait for the first search to finish, then try again.", "err"); return; }
  if (!gh.token) { setMsg("searchMsg", "Connect the app to GitHub first (steps below), then save.", "err"); renderGhPanel(true); return; }
  const btn = $("saveSearchBtn"); btn.disabled = true; setMsg("searchMsg", "Saving…");
  const body = JSON.stringify({ version: 1, saved_at: new Date().toISOString(), ...settings }, null, 1) + "\n";
  const path = `/repos/${META.repo}/contents/settings.json`;
  let result = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    const g = await ghApi("GET", path + "?ref=main");
    if (g.status !== 200 && g.status !== 404) { result = g; break; }
    const sha = g.status === 200 ? str(obj(g.data).sha) : undefined;
    result = await ghApi("PUT", path, { message: "Update search settings from the app", content: b64(body), branch: "main", ...(sha ? { sha } : {}) });
    if (result.status !== 409 && result.status !== 422) break;
  }
  btn.disabled = false;
  if (result && (result.status === 200 || result.status === 201)) {
    S.searchSaved = { settings, at: new Date().toISOString() }; save();
    fillSearchForm();
    setMsg("searchMsg", "Saved. A new search has started with these settings: reload this page in about 3 minutes to see the results.", "ok");
  } else {
    setMsg("searchMsg", ghError(result ? result.status : 0), "err");
    if (result && result.status === 401) { gh.set(""); renderGhPanel(true); }
  }
}
function renderGhPanel(open) {
  const box = $("ghPanel"), repo = META.repo || "your repository", name = str(META.repo).split("/")[1] || "your repository";
  if (gh.token) {
    box.replaceChildren(el("p", null, el("strong", null, "Connected to GitHub. "), `Saving updates ${repo}. The key is stored in this browser only. `,
      el("button", { type: "button", class: "quiet", onclick: () => { gh.set(""); renderGhPanel(true); setMsg("searchMsg", "Disconnected. The key is no longer stored in this browser; you can also delete it on GitHub.", "ok"); } }, "Disconnect")));
    return;
  }
  const input = el("input", { id: "ghToken", type: "password", autocomplete: "off", placeholder: "github_pat_…", "aria-label": "GitHub key" });
  const msg = el("span", { class: "status-msg", id: "ghMsg", role: "status" });
  const connect = async () => {
    const t = input.value.trim();
    if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(t)) { msg.textContent = "That doesn't look like a GitHub key. It starts with github_pat_."; msg.className = "status-msg err"; return; }
    gh.set(t); msg.textContent = "Checking…"; msg.className = "status-msg";
    const r = META.repo ? await ghApi("GET", `/repos/${META.repo}`) : { status: 200 };
    if (r.status === 200) { renderGhPanel(false); setMsg("searchMsg", "Connected. Now press Save and search now.", "ok"); }
    else { gh.set(""); msg.textContent = ghError(r.status); msg.className = "status-msg err"; }
  };
  box.replaceChildren(
    el("p", null, el("strong", null, "Connect to GitHub to save (one time). "), "The search runs in your GitHub repository, so the app needs a key that can update it."),
    el("details", { open: open ? true : null }, el("summary", { class: "small" }, "How to create the key"),
      el("ol", null,
        el("li", null, "Open ", el("a", { href: "https://github.com/settings/personal-access-tokens/new", target: "_blank", rel: "noopener" }, "github.com/settings/personal-access-tokens/new"), " (on a phone, use the desktop site)."),
        el("li", null, "Token name: ", el("code", null, "Sponsor Radar"), ". Expiration: 1 year."),
        el("li", null, "Repository access: ", el("strong", null, "Only select repositories"), ", then pick ", el("strong", null, name), "."),
        el("li", null, "Permissions: add ", el("strong", null, "Contents"), " and set it to ", el("strong", null, "Read and write"), ". Leave everything else as it is."),
        el("li", null, "Press ", el("strong", null, "Generate token"), ", copy it, and paste it below.")),
      el("p", { class: "help" }, "The key can only change files in this one repository. It's kept in this browser only, never in backups, and you can delete it on GitHub at any time.")),
    el("div", { class: "row" }, input, el("button", { type: "button", onclick: connect }, "Connect"), msg));
}
function wireSearch() {
  $("searchForm").addEventListener("submit", saveSearch);
  $("searchForm").addEventListener("input", (e) => { if (e.target.dataset.k === "searches") countSearches(); });
  $("addTargetBtn").addEventListener("click", () => { const all = readTargets(); all.push({ name: "", searches: [], min_salary: NEW_ENTRANT_FLOOR }); drawTargets(all); });
  $("s_anywhere").addEventListener("change", (e) => { $("s_places").disabled = e.target.checked; if (!e.target.checked) $("s_places").focus(); });
}

// ------------------------------------------------------------------ boot
function wire() {
  $("q").addEventListener("input", (e) => { UI.q = e.target.value; UI.shown = PAGE; clearTimeout(wire.t); wire.t = setTimeout(renderRoles, 120); });
  $("familyFilter").addEventListener("change", (e) => { UI.family = e.target.value; UI.shown = PAGE; renderRoles(); });
  $("sponsorFilter").addEventListener("change", (e) => { UI.sponsor = e.target.value; UI.shown = PAGE; renderRoles(); });
  $("sort").addEventListener("change", (e) => { UI.sort = e.target.value; renderRoles(); });
  $("salaryOnly").addEventListener("change", (e) => { UI.salaryOnly = e.target.checked; UI.shown = PAGE; renderRoles(); });
  $("more").addEventListener("click", () => { UI.shown += PAGE; renderRoles(); });
  $("cvFile").addEventListener("change", onCvFile);
  $("toOutline").addEventListener("click", makeOutline);
  $("cvSave").addEventListener("click", saveCv);
  $("detailsForm").addEventListener("submit", saveDetails);
  $("detailsForm").addEventListener("change", () => { $("wsSentence").textContent = wsSentence(); });
  $("resetTemplate").addEventListener("click", () => { $("d_template").value = DEFAULT_TEMPLATE; });
  $("backupBtn").addEventListener("click", backup);
  $("restoreFile").addEventListener("change", restore);
  $("clearBtn").addEventListener("click", () => { $("clearConfirm").hidden = false; });
  $("clearNo").addEventListener("click", () => { $("clearConfirm").hidden = true; });
  $("clearYes").addEventListener("click", clearAll);
}
async function boot() {
  try { if (!sessionStorage.getItem("sr.session")) { S.visits.prev = S.visits.last || null; S.visits.last = new Date().toISOString(); sessionStorage.setItem("sr.session", "1"); save(); } }
  catch { /* private mode: every visit counts as new */ }
  wire(); wireSearch(); fillDetails();
  $("cvRaw").value = S.cvRaw; $("cvOutline").value = S.cvOutline;
  const [jobs, meta, skills, search] = await Promise.all([
    getJson("data/jobs.json").catch((e) => ({ __error: e })), getJson("data/meta.json").catch(() => ({})), getJson("skills.json").catch(() => []),
    getJson("data/settings.json").catch(() => null),
  ]);
  SEARCH = search && Array.isArray(search.targets) ? search : null;
  compileSkills(skills);
  if (jobs.__error && !/404/.test(String(jobs.__error.message))) LOAD_ERROR = String(jobs.__error.message);
  META = obj(meta);
  JOBS = arr(obj(jobs).jobs).map(sanitizeJob).filter(Boolean);
  BY_ID = new Map(JOBS.map((j) => [j.id, j]));
  for (const id of Object.keys(S.statuses)) if (BY_ID.has(id)) S.snapshots[id] = { ...snapshotOf(BY_ID.get(id)), fitPct: obj(S.snapshots[id]).fitPct ?? null };
  rankAll();
  for (const id of Object.keys(S.statuses)) if (BY_ID.has(id)) S.snapshots[id].fitPct = BY_ID.get(id)._fitPct;
  save();
  renderAll(); renderCvPreview(); fillSearchForm(); renderGhPanel(false);
  document.documentElement.dataset.ready = "1";
}
window.__radar = { parseOutline, rawToOutline, tailor: (id) => tailor(BY_ID.get(id)), renderTemplate, clause, article, PREFS, get S() { return S; }, buildPdf, findSkills };
boot();
})();
