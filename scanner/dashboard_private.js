/* ---------- your own stock, listings and money: Stock, Listings and Finance tabs ----------
   None of this is in the page or in the public repository. It lives in data/stock.json (plus photos/ and
   invoices/) of your PRIVATE repository, which the page reads and writes with a key (token) that can only
   touch that repository. The key is stored only in this browser; you connect once per device. */
(function () {
  "use strict";
  var AD = window.__AD;
  if (!AD) return;
  var el = AD.el, money = AD.money, toast = AD.toast, store = AD.store, safeUrl = AD.safeUrl;
  var API = "https://api.github.com";
  var DATA_PATH = "data/stock.json";
  var STALE_DAYS = 7;  /* after a week an ad has sunk in the search results: time to place it again */
  var PLATFORMS = [
    { id: "marktplaats", label: "Marktplaats" },
    { id: "2dehands", label: "2dehands" },
    { id: "vinted", label: "Vinted" },
    { id: "other", label: "Other" }
  ];
  var STATUS = { stock: "For sale", kept: "Kept for myself", sold: "Sold" };
  var VIEWS = { stock: 1, listings: 1, money: 1 };

  var P = {
    token: store.get("privToken", null),
    repo: store.get("privRepo", "") || (AD.OWNER ? AD.OWNER + "/auction-private" : ""),
    data: null, sha: null, loaded: false, loading: null, error: null, loadedAt: 0, saving: 0
  };
  var pui = Object.assign({ stock: "stock", listings: "online", q: "", cat: "" }, store.get("privUi", {}));
  var view = null;
  var box = document.getElementById("private");

  /* ---------- dates (Dutch calendar days) ---------- */
  var dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Amsterdam", year: "numeric", month: "2-digit", day: "2-digit" });
  var showFmt = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  function today() { return dayFmt.format(new Date()); }
  function validDay(s) { return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s); }
  function daysSince(iso) { return validDay(iso) ? Math.round((Date.parse(today()) - Date.parse(iso)) / 86400000) : null; }
  function showDay(iso) { return validDay(iso) ? showFmt.format(new Date(Date.parse(iso))) : "–"; }
  function ago(iso) {
    var d = daysSince(iso);
    return d == null ? "" : d <= 0 ? "today" : d === 1 ? "yesterday" : d + " days ago";
  }
  function amount(v) {  /* "1.149,00", "1149,5", "1149.50", "€ 300" */
    var s = String(v == null ? "" : v).replace(/[€\s]/g, "");
    if (!s) return null;
    if (s.indexOf(",") >= 0) s = s.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(s);
    return isFinite(n) ? Math.round(n * 100) / 100 : null;
  }
  function money2(v) { return v == null || !isFinite(v) ? "–" : new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" }).format(v); }
  function pctOf(part, whole) { return whole > 0 ? Math.round(part / whole * 100) + "%" : "–"; }

  /* ---------- GitHub ---------- */
  function gh(path, opts) {
    opts = opts || {};
    var headers = { "Accept": opts.raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
                    "Authorization": "Bearer " + P.token, "X-GitHub-Api-Version": "2022-11-28" };
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(API + path, { method: opts.method || "GET", headers: headers, cache: "no-store",
                               body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        if (!r.ok) { var e = new Error("GitHub answered " + r.status); e.status = r.status; throw e; }
        if (opts.raw) return r.blob();
        return r.status === 204 ? null : r.json();
      });
  }
  function filePath(path) { return "/repos/" + P.repo + "/contents/" + path.split("/").map(encodeURIComponent).join("/"); }
  function toB64(text) { return btoa(unescape(encodeURIComponent(text))); }
  function fromB64(b64) { return decodeURIComponent(escape(atob(String(b64).replace(/\s/g, "")))); }
  function emptyData() { return { version: 1, items: [] }; }

  function load() {
    if (P.loading) return P.loading;
    P.loading = gh(filePath(DATA_PATH)).then(function (f) {
      if (f.content) return { sha: f.sha, text: fromB64(f.content) };
      /* over 1 MB the contents API leaves the content out: read the blob instead */
      return gh("/repos/" + P.repo + "/git/blobs/" + f.sha).then(function (b) { return { sha: f.sha, text: fromB64(b.content) }; });
    }, function (e) {
      if (e.status === 404) return { sha: null, text: null };  /* a new, empty repository */
      throw e;
    }).then(function (r) {
      var d = null;
      try { d = r.text ? JSON.parse(r.text) : emptyData(); } catch (e) { throw new Error("data/stock.json in your repository isn't valid JSON"); }
      if (!d || !Array.isArray(d.items)) d = emptyData();
      P.data = d; P.sha = r.sha; P.loaded = true; P.error = null; P.loadedAt = Date.now();
      P.loading = null;
    }, function (e) {
      P.loading = null; P.error = e;
      if (e.status === 401) disconnect("GitHub no longer accepts your key (it may have expired). Create a new one below.");
      throw e;
    });
    return P.loading;
  }

  /* One save at a time. Each change is a function that edits a copy of the data; when another device saved
     in between (GitHub refuses the old version), the data is read again and the change is made once more. */
  var chain = Promise.resolve();
  function change(message, edit) {
    P.saving++; drawStatus();
    var p = chain.then(function () { return attempt(message, edit, 0); });
    chain = p.catch(function () { /* reported below */ });
    return p.then(function () { P.saving--; draw(); }, function (e) { P.saving--; saveFailed(e); draw(); throw e; });
  }
  function attempt(message, edit, tries) {
    var copy = JSON.parse(JSON.stringify(P.data || emptyData()));
    if (edit(copy) === false) return Promise.resolve();
    var body = { message: message, content: toB64(JSON.stringify(copy, null, 1) + "\n") };
    if (P.sha) body.sha = P.sha;
    return gh(filePath(DATA_PATH), { method: "PUT", body: body }).then(function (r) {
      P.data = copy; P.sha = r.content.sha;
    }, function (e) {
      if ((e.status === 409 || e.status === 422) && tries < 2) return load().then(function () { return attempt(message, edit, tries + 1); });
      throw e;
    });
  }
  function saveFailed(e) {
    if (e && e.status === 401) disconnect("GitHub no longer accepts your key (it may have expired). Create a new one below.");
    else if (e && (e.status === 403 || e.status === 404)) toast("GitHub refused to save: this key can't write to " + P.repo + ". Check that Contents is set to Read and write.");
    else toast("Couldn't save. Check your connection and try again.");
  }
  function putFile(path, b64, message) {
    return gh(filePath(path), { method: "PUT", body: { message: message, content: b64 } })
      .then(function (r) { return { path: path, sha: r.content.sha }; });
  }
  function removeFile(f, message) {
    if (!f || !f.path || !f.sha) return Promise.resolve();
    return gh(filePath(f.path), { method: "DELETE", body: { message: message, sha: f.sha } })
      .catch(function (e) { if (e.status !== 404 && e.status !== 409 && e.status !== 422) throw e; });
  }
  var blobs = {};
  function blobUrl(path, type) {
    var k = path + "|" + (type || "");
    if (!blobs[k]) {
      blobs[k] = gh(filePath(path), { raw: true }).then(function (b) { return URL.createObjectURL(type ? new Blob([b], { type: type }) : b); });
      blobs[k].catch(function () { delete blobs[k]; });
    }
    return blobs[k];
  }
  function fileB64(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () { resolve(String(r.result).split(",")[1]); };
      r.onerror = function () { reject(r.error); };
      r.readAsDataURL(file);
    });
  }
  function photoB64(file) {  /* at most 1600 px as JPEG: plenty for Marktplaats and Vinted, a few hundred kB each */
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var s = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
        var c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(img.naturalWidth * s)); c.height = Math.max(1, Math.round(img.naturalHeight * s));
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", 0.85).split(",")[1]);
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("This browser can't read " + file.name)); };
      img.src = url;
    });
  }
  function slug(s) { return String(s || "file").toLowerCase().replace(/[^a-z0-9.]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "file"; }
  function newId() { return "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  /* ---------- the numbers ---------- */
  function statusOf(it) { return STATUS[it.status] ? it.status : "stock"; }
  function qtyOf(it) { return Math.max(1, Math.round(it.qty || 1)); }
  function sales(it) { return Array.isArray(it.sales) ? it.sales : []; }
  function soldQty(it) { return sales(it).reduce(function (s, x) { return s + (x.qty || 1); }, 0); }
  function revenue(it) { return sales(it).reduce(function (s, x) { return s + (x.price || 0); }, 0); }
  /* what the sold part cost you: all of it, or a share for a lot of several pieces (soldCost when you know better) */
  function costOfSold(it) {
    if (!sales(it).length) return 0;
    if (it.soldCost != null) return it.soldCost;
    return qtyOf(it) > 1 ? (it.paid || 0) * Math.min(1, soldQty(it) / qtyOf(it)) : (it.paid || 0);
  }
  function saleCost(it, s) {  /* one sale's share of costOfSold */
    var q = soldQty(it);
    return q ? costOfSold(it) * (s.qty || 1) / q : 0;
  }
  function piecesLeft(it) { return statusOf(it) === "stock" ? Math.max(0, qtyOf(it) - soldQty(it)) : 0; }
  function stockValue(it) { return statusOf(it) === "stock" ? Math.max(0, (it.paid || 0) - costOfSold(it)) : 0; }
  function savedOf(it) { return it.newPrice == null ? null : it.newPrice - ((it.paid || 0) - revenue(it)); }
  function listings(it) { return statusOf(it) === "stock" && Array.isArray(it.listings) ? it.listings : []; }
  function isStale(l) { var d = daysSince(l.since); return d != null && d >= STALE_DAYS; }
  function platformLabel(id) { var p = PLATFORMS.filter(function (x) { return x.id === id; })[0]; return p ? p.label : (id || "Other"); }
  function items() { return P.data ? P.data.items : []; }
  function categories() {
    var set = {};
    (AD.categories || []).forEach(function (c) { set[c] = 1; });
    items().forEach(function (it) { if (it.category) set[it.category] = 1; });
    return Object.keys(set).sort(function (a, b) { return a.localeCompare(b); });
  }
  function matches(it) {
    var q = pui.q.trim().toLowerCase();
    if (pui.cat && (it.category || "") !== pui.cat) return false;
    return !q || [it.name, it.category, it.source, it.invoice, it.notes, it.ad && it.ad.title].join(" ").toLowerCase().indexOf(q) >= 0;
  }

  /* ---------- connecting ---------- */
  function tokenUrl() {
    var q = new URLSearchParams({ name: "Auction dashboard private data", target_name: AD.OWNER || "", expires_in: "366", contents: "write",
      description: "Lets the auction dashboard keep your stock, listings and sales in your private repository " + P.repo + "." });
    return "https://github.com/settings/personal-access-tokens/new?" + q.toString();
  }
  function disconnect(message) {
    P.token = null; store.set("privToken", null);
    P.data = null; P.loaded = false; P.sha = null;
    connectMessage = message || "";
    draw();
  }
  var connectMessage = "";
  function connectCard() {
    var repoIn = el("input", { type: "text", id: "privRepo", value: P.repo, spellcheck: "false", autocomplete: "off" });
    var tokIn = el("input", { type: "password", id: "privToken", placeholder: "github_pat_…", spellcheck: "false", autocomplete: "off" });
    var err = el("p", { "class": "error", hidden: !connectMessage, text: connectMessage });
    var link = el("a", { href: tokenUrl(), target: "_blank", rel: "noopener", text: "Create the key on GitHub ↗" });
    repoIn.addEventListener("input", function () { P.repo = repoIn.value.trim(); link.href = tokenUrl(); });
    var ok = el("button", { type: "submit", "class": "primary", text: "Connect" });
    var form = el("form", { "class": "panel pconnect" },
      el("h2", { text: "Connect your private repository" }),
      el("p", null, "Your stock, purchase prices, invoices, ad texts, photos and profit are personal, and this dashboard is public. So they're kept in a ",
        el("strong", { text: "private" }), " GitHub repository of yours. This page reads and writes it with a key that can only touch that repository. You connect once per device (laptop, phone)."),
      el("ol", null,
        el("li", null, "Your private repository: ", el("label", { "for": "privRepo", "class": "sr", text: "Private repository" }), repoIn,
          el("div", { "class": "small" }, "Don't have one yet? ", el("a", { href: "https://github.com/new", target: "_blank", rel: "noopener", text: "Create it on GitHub ↗" }), " and choose Private.")),
        el("li", null, link, ". Name, 1-year expiry and the permission ", el("strong", { text: "Contents: Read and write" }), " are filled in for you."),
        el("li", null, "Under ", el("strong", { text: "Repository access" }), " choose ", el("strong", { text: "Only select repositories" }), " and pick your private repository."),
        el("li", null, "Click ", el("strong", { text: "Generate token" }), ", copy it and paste it here: ", el("label", { "for": "privToken", "class": "sr", text: "GitHub key" }), tokIn)),
      err,
      el("p", { "class": "fine", text: "The key is stored only in this browser. It can't see or change anything outside that one repository." }),
      el("div", { "class": "buttons" }, ok));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var token = tokIn.value.trim(), repo = repoIn.value.trim().replace(/^https:\/\/github\.com\//i, "").replace(/\/+$/, "");
      var show = function (t) { err.textContent = t; err.hidden = !t; };
      if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) { show("Fill in the repository as owner/name, for example " + (AD.OWNER || "you") + "/auction-private."); return; }
      if (!/^(github_pat_|ghp_)\w{20,}$/.test(token)) { show("That doesn't look like a GitHub key. It starts with github_pat_."); return; }
      if (AD.REPO && repo.toLowerCase() === AD.REPO.toLowerCase()) { show(repo + " is the public dashboard repository. Use a separate private one."); return; }
      ok.disabled = true; show("");
      P.token = token; P.repo = repo;
      gh("/repos/" + repo).then(function (r) {
        if (!r.private) { var e = new Error("public"); e.public = true; throw e; }
        return load();
      }).then(function () {
        store.set("privToken", token); store.set("privRepo", repo); connectMessage = "";
        toast("Connected to " + repo + ".");
        draw();
      }, function (e) {
        P.token = null; ok.disabled = false;
        show(e && e.public ? repo + " is public, so everything in it can be read by anyone. Make it private on GitHub (Settings → Danger zone → Change visibility) or pick another repository." :
          e && e.status === 401 ? "GitHub doesn't accept this key. Copy it again, or create a new one." :
          e && (e.status === 403 || e.status === 404) ? "This key can't open " + repo + ". Check step 3 (only select repositories: your private one) and the name in step 1." :
          "Couldn't reach GitHub (" + (e && e.message || "error") + "). Check your connection and try again.");
      });
    });
    return form;
  }

  /* ---------- layout ---------- */
  var bar = null, content = null, statusEl = null, qIn = null, catSel = null;
  function setUi(k, v) { pui[k] = v; store.set("privUi", pui); drawContent(); }
  function toolbar() {
    qIn = el("input", { type: "search", placeholder: "Search your items", "aria-label": "Search your items", value: pui.q });
    qIn.addEventListener("input", function () { setUi("q", qIn.value); });
    catSel = el("select", { "aria-label": "Category" });
    catSel.addEventListener("change", function () { setUi("cat", catSel.value); });
    statusEl = el("span", { "class": "small pstatus" });
    bar = el("div", { "class": "panel ptoolbar" },
      el("div", { "class": "filters" }, qIn, catSel,
        el("button", { type: "button", "class": "pbtn primary", text: "+ Add purchase", onclick: function () { editItem(null); } }),
        el("button", { type: "button", "class": "btn-link", text: "Refresh", onclick: function () { load().then(draw, function () { toast("Couldn't load your data."); }); } })),
      statusEl);
    return bar;
  }
  function fillCats() {
    var cur = pui.cat;
    catSel.textContent = "";
    catSel.append(el("option", { value: "", text: "All categories" }));
    categories().forEach(function (c) { catSel.append(el("option", { value: c, text: c })); });
    catSel.value = cur;
    if (catSel.value !== cur) { pui.cat = ""; catSel.value = ""; }
  }
  function drawStatus() {
    if (!statusEl) return;
    statusEl.textContent = "";
    statusEl.append(P.saving ? "Saving…" : "Saved in your private repository " + P.repo + ". ",
      P.saving ? "" : el("button", { type: "button", "class": "btn-link", text: "Disconnect this device", onclick: function () {
        if (confirm("Forget the key on this device? Your data stays in " + P.repo + ".")) disconnect();
      } }));
  }
  function counts() {
    var cs = document.getElementById("count-stock"), cl = document.getElementById("count-listings");
    if (!P.loaded) { cs.textContent = ""; cl.textContent = ""; return; }
    cs.textContent = String(items().filter(function (it) { return statusOf(it) === "stock"; }).length);
    var stale = items().filter(function (it) { return listings(it).some(isStale); }).length;
    cl.textContent = stale ? String(stale) + " ⚠" : String(items().filter(function (it) { return listings(it).length; }).length);
    cl.title = stale ? stale + " online for more than a week" : "";
  }
  function draw() {
    counts();
    if (!VIEWS[view]) return;
    if (!P.token) { box.textContent = ""; bar = content = null; box.append(connectCard()); return; }
    if (!P.loaded) {
      box.textContent = ""; bar = content = null;
      box.append(el("div", { "class": "panel pempty" }, P.error ? "Couldn't load your data from " + P.repo + " (" + P.error.message + "). " : "Loading your data…",
        P.error ? el("button", { type: "button", "class": "btn-link", text: "Try again", onclick: function () { P.error = null; draw(); } }) : null));
      if (!P.error) load().then(draw, draw);
      return;
    }
    if (!bar || !box.contains(bar)) {
      box.textContent = "";
      box.append(toolbar());
      content = el("div", { "class": "pcontent" });
      box.append(content);
    }
    fillCats();
    drawStatus();
    drawContent();
  }
  function drawContent() {
    if (!content) return;
    content.textContent = "";
    if (view === "stock") stockView(content);
    else if (view === "listings") listingsView(content);
    else moneyView(content);
  }
  function tiles(list) {
    var box = el("section", { "class": "kpis" });
    list.forEach(function (t) {
      box.append(el("div", { "class": "tile" + (t.primary ? " primary" : "") }, el("div", { "class": "label", text: t.label }),
        el("div", { "class": "value num", text: t.value }), t.note ? el("div", { "class": "note", text: t.note }) : null));
    });
    return box;
  }
  function subtabs(defs, key) {
    var row = el("div", { "class": "subtabs", role: "group" });
    defs.forEach(function (d) {
      row.append(el("button", { type: "button", "class": "subtab", "aria-pressed": pui[key] === d.id ? "true" : "false", onclick: function () { setUi(key, d.id); } },
        d.label, el("span", { "class": "count", text: String(d.count) })));
    });
    return row;
  }
  function thumb(it) {
    var ph = (it.photos || [])[0];
    if (!ph) return el("div", { "class": "thumb empty", "aria-hidden": "true", text: "▦" });
    var img = el("img", { "class": "thumb", alt: "" });
    blobUrl(ph.path).then(function (u) { img.src = u; }, function () { img.replaceWith(el("div", { "class": "thumb empty", "aria-hidden": "true", text: "▦" })); });
    return img;
  }
  function badge(l) {
    var d = daysSince(l.since);
    return el("span", { "class": "pbadge p-" + slug(l.platform) + (isStale(l) ? " stale" : ""), title: "Online since " + showDay(l.since) },
      platformLabel(l.platform) + (d != null ? " · " + d + "d" : ""));
  }
  function invoiceLink(it) {
    if (!it.invoiceFile || !it.invoiceFile.path) return it.invoice ? el("span", { "class": "small", text: "Invoice " + it.invoice }) : null;
    return el("button", { type: "button", "class": "btn-link small", text: "📄 " + (it.invoice || "Invoice"), onclick: function () { openFile(it.invoiceFile.path, "application/pdf"); } });
  }
  function openFile(path, type) {
    var w = window.open("", "_blank");  /* opened now, while the click still counts, so no pop-up blocker */
    blobUrl(path, type).then(function (u) { if (w) w.location = u; else location.href = u; },
      function () { if (w) w.close(); toast("Couldn't open the file."); });
  }

  /* ---------- Stock ---------- */
  function stockView(box) {
    var all = items().filter(matches);
    var forSale = all.filter(function (it) { return statusOf(it) === "stock"; });
    var unlisted = forSale.filter(function (it) { return !listings(it).length; });
    var oldest = forSale.reduce(function (m, it) { var d = daysSince(it.bought); return d != null && d > m ? d : m; }, 0);
    var pieces = forSale.reduce(function (s, it) { return s + piecesLeft(it); }, 0);
    box.append(tiles([
      { label: "For sale", value: String(pieces), note: forSale.length + (forSale.length === 1 ? " item" : " items") + (pieces !== forSale.length ? " (lots count per piece)" : ""), primary: true },
      { label: "Money in stock", value: money(forSale.reduce(function (s, it) { return s + stockValue(it); }, 0)), note: "what you paid for what's still for sale" },
      { label: "Not listed yet", value: String(unlisted.length), note: unlisted.length ? "add an ad under Listings" : "everything is online" },
      { label: "Longest in stock", value: forSale.length ? oldest + " days" : "–", note: "since you bought it" }
    ]));
    var defs = [
      { id: "stock", label: "For sale", count: forSale.length },
      { id: "kept", label: "Kept for myself", count: all.filter(function (it) { return statusOf(it) === "kept"; }).length },
      { id: "sold", label: "Sold", count: all.filter(function (it) { return statusOf(it) === "sold"; }).length },
      { id: "all", label: "All", count: all.length }
    ];
    var panel = el("section", { "class": "panel list" }, subtabs(defs, "stock"));
    var shown = all.filter(function (it) { return pui.stock === "all" || statusOf(it) === pui.stock; })
      .sort(function (a, b) { return String(b.bought || "").localeCompare(String(a.bought || "")); });
    panel.append(el("div", { "class": "prow head", "aria-hidden": "true" }, el("div"), el("div", { text: "Item" }), el("div", { text: "Bought" }),
      el("div", { text: "Paid" }), el("div", { text: "Online" }), el("div")));
    shown.forEach(function (it) { panel.append(stockRow(it)); });
    if (!shown.length) panel.append(el("p", { "class": "empty" }, items().length ? "Nothing here with these filters." : "No purchases yet. ",
      items().length ? null : el("button", { type: "button", "class": "btn-link", text: "Add your first purchase", onclick: function () { editItem(null); } })));
    box.append(panel);
  }
  function stockRow(it) {
    var st = statusOf(it), q = qtyOf(it), left = piecesLeft(it), ls = listings(it);
    return el("article", { "class": "prow" },
      el("div", { "class": "c-thumb" }, thumb(it)),
      el("div", { "class": "c-main" },
        el("button", { type: "button", "class": "title plain", text: it.name || "(no name)", onclick: function () { editItem(it); } }),
        el("div", { "class": "meta" }, it.category ? el("span", { "class": "tag", text: it.category }) : null,
          st !== "stock" ? el("span", { "class": "tag muted", text: STATUS[st] }) : null,
          q > 1 ? el("span", { text: (st === "stock" ? left + " of " : "") + q + " pieces" }) : null,
          it.source ? el("span", { "class": "auction", text: it.source }) : null, invoiceLink(it))),
      el("div", { "data-label": "Bought" }, el("div", { "class": "num", text: showDay(it.bought) }), el("div", { "class": "small", text: ago(it.bought) })),
      el("div", { "data-label": "Paid" }, el("div", { "class": "strong num", text: money2(it.paid) }),
        q > 1 ? el("div", { "class": "small", text: money2((it.paid || 0) / q) + " per piece" }) : null,
        sales(it).length ? el("div", { "class": "small", text: "sold for " + money2(revenue(it)) }) : null),
      el("div", { "data-label": "Online" }, st !== "stock" ? el("span", { "class": "small", text: "–" }) :
        ls.length ? el("div", { "class": "badges" }, ls.map(badge)) : el("span", { "class": "small soon", text: "Not listed" })),
      el("div", { "class": "c-actions" },
        st === "stock" ? el("button", { type: "button", "class": "pbtn", text: "Sold…", onclick: function () { sellItem(it); } }) : null,
        el("button", { type: "button", "class": "pbtn", text: "Edit", onclick: function () { editItem(it); } })));
  }

  /* ---------- Listings ---------- */
  function listingsView(box) {
    var forSale = items().filter(function (it) { return statusOf(it) === "stock" && matches(it); });
    var online = forSale.filter(function (it) { return listings(it).length; });
    var stale = online.filter(function (it) { return listings(it).some(isStale); });
    var unlisted = forSale.filter(function (it) { return !listings(it).length; });
    var panel = el("section", { "class": "panel list" }, subtabs([
      { id: "online", label: "Online", count: online.length },
      { id: "stale", label: "Online > " + STALE_DAYS + " days", count: stale.length },
      { id: "unlisted", label: "Not listed yet", count: unlisted.length }
    ], "listings"));
    var help = { online: "Everything you have online, oldest ad first.",
      stale: "These ads have been online for a week or more and have probably sunk in the search results. Place them again (delete the old ad and post it anew), then tap “Placed again today”.",
      unlisted: "For sale but not online yet. Write the ad once here, with photos, so you can post it on every site in a minute." }[pui.listings];
    if (help) panel.append(el("p", { "class": "fav-note", text: help }));
    var list = pui.listings === "stale" ? stale : pui.listings === "unlisted" ? unlisted : online;
    var oldestAd = function (it) { return listings(it).reduce(function (m, l) { return l.since && l.since < m ? l.since : m; }, "9999"); };
    list.slice().sort(function (a, b) { return oldestAd(a).localeCompare(oldestAd(b)); }).forEach(function (it) { panel.append(listingCard(it)); });
    if (!list.length) panel.append(el("p", { "class": "empty", text: pui.listings === "stale" ? "No ads older than a week. 👍" :
      pui.listings === "unlisted" ? "Everything for sale is online." : "Nothing online yet. Add an ad from “Not listed yet”." }));
    box.append(panel);
  }
  function listingCard(it) {
    var card = el("article", { "class": "pcard" });
    var head = el("div", { "class": "pcard-head" }, thumb(it),
      el("div", { "class": "c-main" },
        el("button", { type: "button", "class": "title plain", text: it.name || "(no name)", onclick: function () { editItem(it); } }),
        el("div", { "class": "meta" }, it.category ? el("span", { "class": "tag", text: it.category }) : null,
          el("span", { text: "paid " + money2(it.paid) + (qtyOf(it) > 1 ? " for " + qtyOf(it) + " pieces" : "") }),
          el("span", { text: "in stock " + (daysSince(it.bought) != null ? daysSince(it.bought) + " days" : "–") }))),
      el("div", { "class": "c-actions" }, el("button", { type: "button", "class": "pbtn", text: "Sold…", onclick: function () { sellItem(it); } })));
    card.append(head);

    var rows = el("div", { "class": "plistings" });
    listings(it).forEach(function (l) {
      var url = safeUrl(l.url), d = daysSince(l.since);
      rows.append(el("div", { "class": "plisting" + (isStale(l) ? " stale" : "") },
        el("span", { "class": "pbadge p-" + slug(l.platform), text: platformLabel(l.platform) }),
        el("span", { "class": "num strong", text: l.price != null ? money2(l.price) : "–" }),
        el("span", { "class": isStale(l) ? "small soon" : "small", text: "online since " + showDay(l.since) + (d != null ? " (" + (d === 0 ? "today" : d + " days") + ")" : "") }),
        url ? el("a", { href: url, target: "_blank", rel: "noopener", text: "Open ad ↗" }) : null,
        el("span", { "class": "spacer" }),
        el("button", { type: "button", "class": "pbtn", text: "Placed again today", title: "You deleted the old ad and posted it again", onclick: function () {
          change("Relisted " + (it.name || it.id) + " on " + platformLabel(l.platform), function (data) {
            var x = find(data, it.id), m = x && (x.listings || []).filter(function (y) { return y.id === l.id; })[0];
            if (!m) return false;
            m.since = today(); m.relisted = (m.relisted || 0) + 1;
          }).then(function () { toast("Marked as placed again today."); }, function () {});
        } }),
        el("button", { type: "button", "class": "btn-link", text: "Remove", onclick: function () {
          change("Ad offline: " + (it.name || it.id) + " on " + platformLabel(l.platform), function (data) {
            var x = find(data, it.id); if (!x) return false;
            x.listings = (x.listings || []).filter(function (y) { return y.id !== l.id; });
          }).catch(function () {});
        } })));
    });
    rows.append(addListingForm(it));
    card.append(rows);
    card.append(adBlock(it));
    return card;
  }
  function addListingForm(it) {
    var used = listings(it).map(function (l) { return l.platform; });
    var sel = el("select", { "aria-label": "Site" });
    PLATFORMS.forEach(function (p) { sel.append(el("option", { value: p.id, text: p.label })); });
    var free = PLATFORMS.filter(function (p) { return used.indexOf(p.id) < 0; })[0];
    sel.value = free ? free.id : "other";
    var price = el("input", { type: "text", inputmode: "decimal", placeholder: "Asking price €", "aria-label": "Asking price", value: it.ad && it.ad.price != null ? String(it.ad.price) : "" });
    var since = el("input", { type: "date", value: today(), "aria-label": "Online since" });
    var url = el("input", { type: "url", placeholder: "Link to the ad (optional)", "aria-label": "Link to the ad" });
    var form = el("form", { "class": "padd" }, el("span", { "class": "small strong", text: "Add an ad:" }), sel, price, since, url,
      el("button", { type: "submit", "class": "pbtn primary", text: "Add" }));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var entry = { id: newId(), platform: sel.value, since: validDay(since.value) ? since.value : today(), price: amount(price.value),
                    url: safeUrl(url.value.trim()) || null };
      change("Online: " + (it.name || it.id) + " on " + platformLabel(entry.platform), function (data) {
        var x = find(data, it.id); if (!x) return false;
        x.listings = (x.listings || []).concat([entry]);
        if (entry.price != null) { x.ad = x.ad || {}; x.ad.price = entry.price; }
      }).then(function () { toast("Ad added."); }, function () {});
    });
    return form;
  }
  function adBlock(it) {
    var ad = it.ad || {}, photos = it.photos || [];
    var block = el("div", { "class": "pad" });
    var text = [ad.title, ad.text].filter(Boolean).join("\n\n");
    block.append(el("div", { "class": "pad-head" }, el("strong", { text: "Your ad" }),
      ad.price != null ? el("span", { "class": "small", text: "asking " + money2(ad.price) }) : null,
      el("span", { "class": "spacer" }),
      ad.title ? copyBtn("Copy title", ad.title) : null,
      ad.text ? copyBtn("Copy text", ad.text) : null,
      photos.length ? el("button", { type: "button", "class": "pbtn", text: (navigator.canShare ? "Share" : "Save") + " " + photos.length + " photo" + (photos.length > 1 ? "s" : ""), onclick: function () { sharePhotos(it); } }) : null,
      el("button", { type: "button", "class": "btn-link", text: text || photos.length ? "Edit ad" : "Write the ad", onclick: function () { editItem(it, "ad"); } })));
    if (ad.title) block.append(el("div", { "class": "pad-title", text: ad.title }));
    if (ad.text) block.append(el("div", { "class": "pad-text", text: ad.text }));
    if (photos.length) {
      var strip = el("div", { "class": "pstrip" });
      photos.forEach(function (ph) {
        var img = el("img", { alt: "", "class": "pphoto" });
        blobUrl(ph.path).then(function (u) { img.src = u; }, function () { img.remove(); });
        strip.append(img);
      });
      block.append(strip);
    }
    if (!text && !photos.length) block.append(el("p", { "class": "small", text: "No ad text or photos saved yet." }));
    return block;
  }
  function copyBtn(label, value) {
    var b = el("button", { type: "button", "class": "pbtn", text: label });
    b.addEventListener("click", function () {
      var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = label; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(value).then(done, function () { toast("Couldn't copy."); });
    });
    return b;
  }
  /* on a phone: straight into the Marktplaats or Vinted app; on a laptop: saved to your downloads */
  function sharePhotos(it) {
    var photos = it.photos || [];
    Promise.all(photos.map(function (ph, i) {
      return blobUrl(ph.path, "image/jpeg").then(function (u) { return fetch(u).then(function (r) { return r.blob(); }); })
        .then(function (b) { return new File([b], slug(it.name) + "-" + (i + 1) + ".jpg", { type: "image/jpeg" }); });
    })).then(function (files) {
      if (navigator.canShare && navigator.canShare({ files: files })) {
        return navigator.share({ files: files, title: (it.ad && it.ad.title) || it.name }).catch(function () { /* cancelled */ });
      }
      files.forEach(function (f) {
        var a = el("a", { href: URL.createObjectURL(f), download: f.name });
        document.body.append(a); a.click(); a.remove();
      });
    }, function () { toast("Couldn't load the photos."); });
  }

  /* ---------- Finance ---------- */
  function moneyView(box) {
    var all = items().filter(matches);
    var rows = [];
    all.forEach(function (it) {
      sales(it).forEach(function (s) {
        rows.push({ it: it, s: s, date: validDay(s.date) ? s.date : null, cost: saleCost(it, s), price: s.price || 0 });
      });
    });
    var rev = rows.reduce(function (t, r) { return t + r.price; }, 0);
    var cost = rows.reduce(function (t, r) { return t + r.cost; }, 0);
    var withNew = all.filter(function (it) { return it.newPrice != null; });
    var saved = withNew.reduce(function (t, it) { return t + savedOf(it); }, 0);
    var inStock = all.reduce(function (t, it) { return t + stockValue(it); }, 0);
    box.append(tiles([
      { label: "Earned", value: money(rev - cost), note: "profit on what you sold: " + pctOf(rev - cost, cost) + " of what it cost you", primary: true },
      { label: "Sold for", value: money(rev), note: rows.length + (rows.length === 1 ? " sale" : " sales") + ", bought for " + money(cost) },
      { label: "Saved", value: money(saved), note: "new price minus what you paid (net of resales), " + withNew.length + " items with a new price" },
      { label: "Money in stock", value: money(inStock), note: "paid for what's still for sale" }
    ]));

    var chartPanel = el("section", { "class": "panel pchart" }, el("h2", { text: "Profit over time" }));
    chartPanel.append(profitChart(rows));
    box.append(chartPanel);

    /* per category */
    var cats = {};
    rows.forEach(function (r) {
      var c = r.it.category || "Other", g = cats[c] = cats[c] || { n: 0, rev: 0, cost: 0, days: [] };
      g.n += r.s.qty || 1; g.rev += r.price; g.cost += r.cost;
      var d = r.date && validDay(r.it.bought) ? Math.round((Date.parse(r.date) - Date.parse(r.it.bought)) / 86400000) : null;
      if (d != null && d >= 0) g.days.push(d);
    });
    var catTable = el("table", { "class": "ptable" }, el("thead", null, el("tr", null,
      ["Category", "Sold", "Sold for", "Profit", "Margin", "Days to sell"].map(function (h, i) { return el("th", { "class": i ? "amt" : "", text: h }); }))));
    var tb = el("tbody");
    Object.keys(cats).sort(function (a, b) { return (cats[b].rev - cats[b].cost) - (cats[a].rev - cats[a].cost); }).forEach(function (c) {
      var g = cats[c], med = g.days.length ? g.days.slice().sort(function (a, b) { return a - b; })[Math.floor(g.days.length / 2)] : null;
      tb.append(el("tr", null, el("td", { text: c }), el("td", { "class": "amt", text: String(g.n) }), el("td", { "class": "amt", text: money(g.rev) }),
        el("td", { "class": "amt " + (g.rev - g.cost >= 0 ? "good" : "bad"), text: money(g.rev - g.cost) }), el("td", { "class": "amt", text: pctOf(g.rev - g.cost, g.cost) }),
        el("td", { "class": "amt", text: med != null ? med + " (median)" : "–" })));
    });
    catTable.append(tb);
    box.append(el("section", { "class": "panel ptablebox" }, el("h2", { text: "Per category" }),
      rows.length ? el("div", { "class": "scrollx" }, catTable) : el("p", { "class": "empty", text: "No sales yet. Tap “Sold…” on an item when it's sold." })));

    /* every sale */
    var saleTable = el("table", { "class": "ptable" }, el("thead", null, el("tr", null,
      ["Sold on", "Item", "Where", "Sold for", "Cost", "Profit", "Margin"].map(function (h, i) { return el("th", { "class": i > 2 ? "amt" : "", text: h }); }))));
    var sb = el("tbody");
    rows.slice().sort(function (a, b) { return String(b.date || b.it.bought || "").localeCompare(String(a.date || a.it.bought || "")); }).forEach(function (r) {
      var p = r.price - r.cost;
      sb.append(el("tr", null,
        el("td", { text: r.date ? showDay(r.date) : "date unknown" }),
        el("td", null, el("button", { type: "button", "class": "btn-link", text: r.it.name, onclick: function () { editItem(r.it); } }),
          (r.s.qty || 1) > 1 ? el("span", { "class": "small", text: " × " + r.s.qty }) : null,
          r.s.note ? el("div", { "class": "small", text: r.s.note }) : null),
        el("td", { text: r.s.platform ? platformLabel(r.s.platform) : "–" }),
        el("td", { "class": "amt", text: money2(r.price) }), el("td", { "class": "amt", text: money2(r.cost) }),
        el("td", { "class": "amt " + (p >= 0 ? "good" : "bad"), text: money2(p) }), el("td", { "class": "amt", text: pctOf(p, r.cost) })));
    });
    saleTable.append(sb);
    if (rows.length) box.append(el("section", { "class": "panel ptablebox" }, el("h2", { text: "Every sale" }), el("div", { "class": "scrollx" }, saleTable),
      el("p", { "class": "small", text: "Margin = profit as a share of what the sold piece cost you, the same as the max bids on the Lots tab. For a lot of several pieces each piece costs its share of the price." })));
  }
  function svg(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    return e;
  }
  /* cumulative profit, one step per sale; sales without a date count from before the first dated one */
  function profitChart(rows) {
    var undated = rows.filter(function (r) { return !r.date; });
    var dated = rows.filter(function (r) { return r.date; }).sort(function (a, b) { return a.date.localeCompare(b.date); });
    var start = undated.reduce(function (t, r) { return t + r.price - r.cost; }, 0);
    if (!dated.length) {
      return el("p", { "class": "small", text: rows.length ? "Earned so far: " + money(start) + ". Sales you record from now on (with their date) build the line here." :
        "Your profit builds up here as you record sales." });
    }
    var pts = [{ t: Date.parse(dated[0].date) - 86400000 * 7, v: start }], run = start;
    dated.forEach(function (r) { run += r.price - r.cost; pts.push({ t: Date.parse(r.date), v: run, r: r }); });
    pts.push({ t: Math.max(Date.parse(today()), pts[pts.length - 1].t), v: run });
    var W = 760, H = 220, L = 56, R = 12, T = 12, B = 28;
    var t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
    var vs = pts.map(function (p) { return p.v; }), lo = Math.min(0, Math.min.apply(null, vs)), hi = Math.max(1, Math.max.apply(null, vs));
    var x = function (t) { return L + (t - t0) / Math.max(1, t1 - t0) * (W - L - R); };
    var y = function (v) { return T + (hi - v) / (hi - lo) * (H - T - B); };
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, "class": "pline", role: "img", "aria-label": "Cumulative profit, now " + money(run) });
    [lo, (lo + hi) / 2, hi].forEach(function (v) {
      s.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), "class": "grid" }));
      s.append(svg("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", text: money(v) }));
    });
    if (lo < 0) s.append(svg("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), "class": "base" }));
    var d = "M" + x(pts[0].t) + "," + y(pts[0].v);
    for (var i = 1; i < pts.length; i++) d += "H" + x(pts[i].t) + "V" + y(pts[i].v);
    s.append(svg("path", { d: d, "class": "line" }));
    pts.forEach(function (p) {
      if (!p.r) return;
      var c = svg("circle", { cx: x(p.t), cy: y(p.v), r: 3.5, "class": "dot" });
      c.append(svg("title", { text: showDay(p.r.date) + ": " + p.r.it.name + " " + money2(p.r.price - p.r.cost) + " · total " + money(p.v) }));
      s.append(c);
    });
    s.append(svg("text", { x: L, y: H - 8, text: showDay(new Date(t0).toISOString().slice(0, 10)) }));
    s.append(svg("text", { x: W - R, y: H - 8, "text-anchor": "end", text: showDay(new Date(t1).toISOString().slice(0, 10)) }));
    return el("div", { "class": "pchart-box" }, s);
  }

  /* ---------- dialogs ---------- */
  /* one invoice can cover several purchases: its file stays while another item still uses it */
  function sharedFile(path, exceptId) {
    return items().some(function (x) { return x.id !== exceptId && x.invoiceFile && x.invoiceFile.path === path; });
  }
  function find(data, id) { return data.items.filter(function (x) { return x.id === id; })[0] || null; }
  var dlg = el("dialog", { "class": "pdlg" });
  document.body.append(dlg);
  function openDlg(form) {
    dlg.textContent = ""; dlg.append(form);
    if (dlg.showModal) { if (!dlg.open) dlg.showModal(); } else dlg.setAttribute("open", "");
  }
  function closeDlg() { if (dlg.close) dlg.close(); else dlg.removeAttribute("open"); }
  function field(label, input, hint) {
    var id = "pf-" + Math.random().toString(36).slice(2, 8);
    input.id = id;
    return el("div", { "class": "pfield" }, el("label", { "for": id, text: label }), input, hint ? el("div", { "class": "small", text: hint }) : null);
  }

  function editItem(it, focus) {
    var isNew = !it;
    it = it ? JSON.parse(JSON.stringify(it)) : { id: newId(), bought: today(), qty: 1, status: "stock", photos: [], listings: [], sales: [] };
    var inp = {
      name: el("input", { type: "text", required: true, value: it.name || "", placeholder: "e.g. HUGO colbert Arti, size 52" }),
      category: el("input", { type: "text", value: it.category || "", list: "pcats", placeholder: "e.g. Suit / formalwear" }),
      bought: el("input", { type: "date", value: validDay(it.bought) ? it.bought : "" }),
      paid: el("input", { type: "text", inputmode: "decimal", value: it.paid != null ? String(it.paid).replace(".", ",") : "", placeholder: "0,00" }),
      qty: el("input", { type: "number", min: "1", step: "1", value: String(qtyOf(it)) }),
      source: el("input", { type: "text", value: it.source || "", placeholder: "e.g. Troostwijk, lot A1-50645-45" }),
      invoice: el("input", { type: "text", value: it.invoice || "", placeholder: "e.g. INV1A0001554278" }),
      invoiceFile: el("input", { type: "file", accept: "application/pdf,image/*" }),
      newPrice: el("input", { type: "text", inputmode: "decimal", value: it.newPrice != null ? String(it.newPrice).replace(".", ",") : "", placeholder: "optional" }),
      status: el("select"),
      notes: el("textarea", { rows: "2" }),
      adTitle: el("input", { type: "text", value: (it.ad && it.ad.title) || "", placeholder: "Title of your ad" }),
      adPrice: el("input", { type: "text", inputmode: "decimal", value: it.ad && it.ad.price != null ? String(it.ad.price).replace(".", ",") : "", placeholder: "Asking price €" }),
      adText: el("textarea", { rows: "6", placeholder: "Description: brand, model, size, condition, pickup or shipping…" }),
      photos: el("input", { type: "file", accept: "image/*", multiple: true })
    };
    inp.notes.value = it.notes || "";
    inp.adText.value = (it.ad && it.ad.text) || "";
    Object.keys(STATUS).forEach(function (k) { inp.status.append(el("option", { value: k, text: STATUS[k] })); });
    inp.status.value = statusOf(it);
    var dl = el("datalist", { id: "pcats" });
    categories().forEach(function (c) { dl.append(el("option", { value: c })); });

    var removed = [], keep = (it.photos || []).slice();
    var strip = el("div", { "class": "pstrip edit" });
    function drawStrip() {
      strip.textContent = "";
      keep.forEach(function (ph) {
        var img = el("img", { alt: "", "class": "pphoto" });
        blobUrl(ph.path).then(function (u) { img.src = u; }, function () {});
        strip.append(el("div", { "class": "pph" }, img, el("button", { type: "button", "class": "pph-x", "aria-label": "Remove photo", text: "✕", onclick: function () {
          keep = keep.filter(function (x) { return x !== ph; }); removed.push(ph); drawStrip();
        } })));
      });
    }
    drawStrip();
    var dropInvoice = false;
    var invNow = it.invoiceFile && it.invoiceFile.path ? el("div", { "class": "small" },
      el("button", { type: "button", "class": "btn-link", text: "📄 Open saved invoice", onclick: function () { openFile(it.invoiceFile.path, "application/pdf"); } }), " · ",
      el("button", { type: "button", "class": "btn-link", text: "Remove", onclick: function (e) { dropInvoice = true; e.target.parentNode.textContent = "Invoice will be removed when you save."; } })) : null;

    var err = el("p", { "class": "error", hidden: true });
    var save = el("button", { type: "submit", "class": "primary", text: isNew ? "Add" : "Save" });
    var form = el("form", { method: "dialog" },
      el("h2", { text: isNew ? "Add a purchase" : "Edit " + (it.name || "item") }),
      el("div", { "class": "pgrid" },
        field("What is it?", inp.name), field("Category", inp.category),
        field("Bought on", inp.bought), field("Paid, all in (€)", inp.paid, "Bid + buyer's premium + VAT + costs"),
        field("Pieces", inp.qty, "For a lot: the price is split over the pieces"), field("Status", inp.status),
        field("Where from", inp.source), field("Invoice number", inp.invoice),
        el("div", { "class": "pfield" }, el("label", { text: "Invoice (PDF)" }), invNow, inp.invoiceFile),
        field("New price (€)", inp.newPrice, "For “saved” in Finance")),
      field("Notes", inp.notes),
      el("h3", { text: "Ad" }),
      el("div", { "class": "pgrid" }, field("Title", inp.adTitle), field("Asking price (€)", inp.adPrice)),
      field("Text", inp.adText),
      el("div", { "class": "pfield" }, el("label", { text: "Photos" }), strip, inp.photos, el("div", { "class": "small", text: "Photos are made smaller (1600 px) before they're saved." })),
      dl, err,
      el("div", { "class": "buttons" },
        isNew ? null : el("button", { type: "button", "class": "danger", text: "Delete", onclick: function () {
          if (!confirm("Delete “" + (it.name || "this item") + "” with its photos and invoice? This can't be undone.")) return;
          closeDlg();
          var files = (it.photos || []).concat(it.invoiceFile && !sharedFile(it.invoiceFile.path, it.id) ? [it.invoiceFile] : []);
          change("Delete " + (it.name || it.id), function (data) { data.items = data.items.filter(function (x) { return x.id !== it.id; }); })
            .then(function () { return Promise.all(files.map(function (f) { return removeFile(f, "Delete file of " + (it.name || it.id)); })); })
            .then(function () { toast("Deleted."); }, function () {});
        } }),
        el("span", { "class": "spacer" }),
        el("button", { type: "button", text: "Cancel", onclick: closeDlg }), save));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var name = inp.name.value.trim();
      if (!name) { err.textContent = "Give it a name."; err.hidden = false; return; }
      var paid = amount(inp.paid.value);
      if (inp.paid.value.trim() && paid == null) { err.textContent = "The price paid isn't a number."; err.hidden = false; return; }
      save.disabled = true; err.hidden = true; save.textContent = "Saving…";
      var stamp = Date.now().toString(36);
      var newPhotos = Array.prototype.slice.call(inp.photos.files || []);
      var invFile = (inp.invoiceFile.files || [])[0];
      var uploads = newPhotos.map(function (f, i) {
        return photoB64(f).then(function (b64) { return putFile("photos/" + it.id + "/" + stamp + "-" + (i + 1) + ".jpg", b64, "Photo of " + name); });
      });
      var invUp = invFile ? fileB64(invFile).then(function (b64) {
        var ext = /pdf$/i.test(invFile.type) || /\.pdf$/i.test(invFile.name) ? ".pdf" : "." + (invFile.name.split(".").pop() || "bin");
        return putFile("invoices/" + it.id + "-" + slug(inp.invoice.value || invFile.name.replace(/\.[^.]+$/, "")) + "-" + stamp + ext, b64, "Invoice of " + name);
      }) : Promise.resolve(null);
      Promise.all([Promise.all(uploads), invUp]).then(function (res) {
        var added = res[0], inv = res[1], oldInv = it.invoiceFile;
        var values = {
          name: name, category: inp.category.value.trim(), bought: validDay(inp.bought.value) ? inp.bought.value : null,
          paid: paid == null ? 0 : paid, qty: Math.max(1, parseInt(inp.qty.value, 10) || 1), source: inp.source.value.trim(),
          invoice: inp.invoice.value.trim(), newPrice: amount(inp.newPrice.value), status: inp.status.value, notes: inp.notes.value.trim(),
          ad: { title: inp.adTitle.value.trim(), text: inp.adText.value.trim(), price: amount(inp.adPrice.value) }
        };
        closeDlg();
        return change((isNew ? "Add " : "Edit ") + name, function (data) {
          var x = find(data, it.id);
          if (!x) { if (!isNew) return false; x = { id: it.id, photos: [], listings: [], sales: [], added: new Date().toISOString() }; data.items.push(x); }
          Object.keys(values).forEach(function (k) { x[k] = values[k]; });
          var gone = removed.map(function (p) { return p.path; });
          x.photos = (x.photos || []).filter(function (p) { return gone.indexOf(p.path) < 0; }).concat(added);
          if (inv || dropInvoice) x.invoiceFile = inv || null;
        }).then(function () {
          var old = removed.concat((inv || dropInvoice) && oldInv && !sharedFile(oldInv.path, it.id) ? [oldInv] : []);
          toast(isNew ? "Added to your stock." : "Saved.");
          return Promise.all(old.map(function (f) { return removeFile(f, "Remove old file of " + name); }));
        });
      }).catch(function (e2) {
        if (dlg.open) { save.disabled = false; save.textContent = isNew ? "Add" : "Save"; err.textContent = "Couldn't save: " + (e2 && e2.message || "error"); err.hidden = false; }
      });
    });
    openDlg(form);
    setTimeout(function () { (focus === "ad" ? inp.adTitle : inp.name).focus(); }, 0);
  }

  function sellItem(it) {
    var left = piecesLeft(it), lots = qtyOf(it) > 1;
    var date = el("input", { type: "date", value: today() });
    var price = el("input", { type: "text", inputmode: "decimal", required: true, placeholder: "0,00" });
    var qty = el("input", { type: "number", min: "1", max: String(Math.max(1, left)), step: "1", value: "1" });
    var where = el("select");
    PLATFORMS.forEach(function (p) { where.append(el("option", { value: p.id, text: p.label })); });
    var ls = listings(it);
    if (ls.length) where.value = ls[0].platform;
    var note = el("input", { type: "text", placeholder: "optional, e.g. which size" });
    var err = el("p", { "class": "error", hidden: true });
    var form = el("form", { method: "dialog" },
      el("h2", { text: "Sold: " + (it.name || "item") }),
      el("div", { "class": "pgrid" }, field("Sold on", date), field("Sold for (€)", price, "What you received"),
        lots ? field("Pieces", qty, left + " of " + qtyOf(it) + " still for sale") : null, field("Where", where), field("Note", note)),
      ls.length ? el("p", { "class": "small", text: "Remember to take the ad offline on " + ls.map(function (l) { return platformLabel(l.platform); }).join(" and ") + "." }) : null,
      err,
      el("div", { "class": "buttons" }, el("span", { "class": "spacer" }), el("button", { type: "button", text: "Cancel", onclick: closeDlg }),
        el("button", { type: "submit", "class": "primary", text: "Save sale" })));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var p = amount(price.value);
      if (p == null) { err.textContent = "Fill in what you sold it for."; err.hidden = false; return; }
      var n = lots ? Math.max(1, Math.min(left || 1, parseInt(qty.value, 10) || 1)) : 1;
      var sale = { date: validDay(date.value) ? date.value : today(), price: p, qty: n, platform: where.value, note: note.value.trim() || undefined };
      closeDlg();
      change("Sold " + (it.name || it.id) + " for " + p, function (data) {
        var x = find(data, it.id); if (!x) return false;
        x.sales = (x.sales || []).concat([sale]);
        if (soldQty(x) >= qtyOf(x)) { x.status = "sold"; x.listings = []; }
      }).then(function () {
        var cost = qtyOf(it) > 1 && it.soldCost == null ? (it.paid || 0) * n / qtyOf(it) : (it.paid || 0);
        toast("Sale saved: " + money2(p - cost) + " profit.");
      }, function () {});
    });
    openDlg(form);
    setTimeout(function () { price.focus(); }, 0);
  }

  /* ---------- wiring ---------- */
  window.__ADP = {
    render: function (v) { view = v; draw(); }
  };
  /* another device may have saved: read again when you come back to this tab after a while */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && P.token && P.loaded && VIEWS[view] && Date.now() - P.loadedAt > 60000 && !P.saving && !dlg.open) {
      load().then(draw, function () {});
    }
  });
  if (P.token) load().then(function () { counts(); if (VIEWS[view]) draw(); }, function () { counts(); });
  if (VIEWS[AD.view()]) { view = AD.view(); draw(); }
})();
