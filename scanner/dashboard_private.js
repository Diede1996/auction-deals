/* ---------- your own pickups, stock, listings and money: Pickups, Stock, Listings and Finance tabs ----------
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
  var VIEWS = { pickup: 1, stock: 1, listings: 1, money: 1 };

  var P = {
    token: store.get("privToken", null),
    repo: store.get("privRepo", "") || (AD.OWNER ? AD.OWNER + "/auction-private" : ""),
    data: null, sha: null, loaded: false, loading: null, error: null, loadedAt: 0, saving: 0
  };
  var pui = Object.assign({ pickup: "todo", stock: "stock", listings: "online", q: "", cat: "" }, store.get("privUi", {}));
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
    return !q || [it.name, it.category, it.source, it.invoice, it.notes, it.ad && it.ad.title, it.lotOf && it.lotOf.name].join(" ").toLowerCase().indexOf(q) >= 0;
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
    var cs = document.getElementById("count-stock"), cl = document.getElementById("count-listings"), cp = document.getElementById("count-pickup");
    if (!P.loaded) { cs.textContent = ""; cl.textContent = ""; cp.textContent = ""; return; }
    cp.textContent = String(stops(false).length || "");
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
    if (view === "pickup") pickupView(content);
    else if (view === "stock") stockView(content);
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
          it.source ? el("span", { "class": "auction", text: it.source }) : null, invoiceLink(it),
          it.lotOf ? el("span", { "class": "small", text: "from lot “" + it.lotOf.name + "”" }) : null,
          pending(it) ? el("span", { "class": "tag", text: "🚚 pickup " + (it.pickup.date ? dayLabel(it.pickup.date) : "not planned") }) : null)),
      el("div", { "data-label": "Bought" }, el("div", { "class": "num", text: showDay(it.bought) }), el("div", { "class": "small", text: ago(it.bought) })),
      el("div", { "data-label": "Paid" }, el("div", { "class": "strong num", text: money2(it.paid) }),
        q > 1 ? el("div", { "class": "small", text: money2((it.paid || 0) / q) + " per piece" }) : null,
        sales(it).length ? el("div", { "class": "small", text: "sold for " + money2(revenue(it)) }) : null),
      el("div", { "data-label": "Online" }, st !== "stock" ? el("span", { "class": "small", text: "–" }) :
        ls.length ? el("div", { "class": "badges" }, ls.map(badge)) : el("span", { "class": "small soon", text: "Not listed" })),
      el("div", { "class": "c-actions" },
        canSplit(it) ? el("button", { type: "button", "class": "pbtn", text: "Split…", title: "Make each piece its own item, with its own ad", onclick: function () { splitItem(it); } }) : null,
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

  /* ---------- Pickups ---------- */
  var MONTHS = { jan: 0, feb: 1, mar: 2, mrt: 2, apr: 3, may: 4, mei: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, okt: 9, nov: 10, dec: 11 };
  var WON_DAYS = 21;  /* favorites that closed in the last three weeks: did you win them? */
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  /* the pickup text of the scan, "Fri 6 Nov, 08:45–12:00" (or Dutch "vr 6 nov 08:45-12:00"), as { date, from, to }.
     The year is the first one that puts the day on or after the closing day. */
  function parseWhen(text, after) {
    var s = String(text || ""), out = {};
    var m = s.match(/(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?/);
    if (m && MONTHS[m[2].toLowerCase()] != null) {
      var base = validDay(after) ? after : today(), y = +base.slice(0, 4);
      var md = "-" + pad(MONTHS[m[2].toLowerCase()] + 1) + "-" + pad(+m[1]);
      out.date = y + md < base ? (y + 1) + md : y + md;
    }
    var t = s.match(/(\d{1,2})[:.](\d{2})\s*[–-]\s*(\d{1,2})[:.](\d{2})/);
    if (t) { out.from = pad(+t[1]) + ":" + t[2]; out.to = pad(+t[3]) + ":" + t[4]; }
    return out;
  }
  var weekdayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
  function dayLabel(iso) {
    var d = -daysSince(iso);
    return d === 0 ? "today" : d === 1 ? "tomorrow" : weekdayFmt.format(new Date(Date.parse(iso)));
  }
  /* a pickup without day or address yet (todo) still shows, so you fill it in */
  function hasPickup(p) { return !!(p && (p.date || p.address || p.todo)); }
  function pending(it) { return hasPickup(it.pickup) && !it.pickup.done; }
  function hours(p) { return p.from ? p.from + (p.to ? "–" + p.to : "") : "time not set"; }
  /* items to collect, as stops: one address on one day, earliest time first */
  function stops(done, filtered) {
    var by = {}, list = [];
    items().forEach(function (it) {
      if (filtered && !matches(it)) return;
      var p = it.pickup;
      if (!hasPickup(p) || !!p.done !== done) return;
      var k = (p.date || "") + "|" + String(p.address || "").trim().toLowerCase();
      if (!by[k]) { by[k] = { key: k, date: p.date || null, address: p.address || "", from: p.from, to: p.to, items: [] }; list.push(by[k]); }
      var s = by[k];
      if (p.from && (!s.from || p.from < s.from)) s.from = p.from;
      if (p.to && (!s.to || p.to > s.to)) s.to = p.to;
      s.items.push(it);
    });
    return list.sort(function (a, b) {
      return String(a.date || "9999").localeCompare(String(b.date || "9999")) || String(a.from || "99").localeCompare(String(b.from || "99"));
    });
  }
  /* distance and driving time from the scan, when one of its lots is at this address */
  function tripTo(address) {
    var a = String(address || "").trim().toLowerCase(), hit = null;
    if (a) (AD.lots ? AD.lots() : []).some(function (l) { if (l.trip && String(l.pickup || "").trim().toLowerCase() === a) { hit = l.trip; return true; } return false; });
    return hit;
  }
  function mapsUrl(address) { return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(address); }
  function routeUrl(list) {  /* from where you are, past every stop of the day in time order */
    var addr = list.map(function (s) { return s.address; }).filter(Boolean);
    var q = { api: "1", destination: addr[addr.length - 1], travelmode: "driving" };
    if (addr.length > 1) q.waypoints = addr.slice(0, -1).join("|");
    return "https://www.google.com/maps/dir/?" + new URLSearchParams(q).toString();
  }

  /* calendar: Dutch times as UTC, so every calendar app reads them the same */
  var amsFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Amsterdam", year: "numeric", month: "2-digit", day: "2-digit",
                                                  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  function amsLocal(t) { var p = {}; amsFmt.formatToParts(new Date(t)).forEach(function (x) { p[x.type] = x.value; }); return Date.parse(p.year + "-" + p.month + "-" + p.day + "T" + p.hour + ":" + p.minute + ":" + p.second + "Z"); }
  function utcStamp(day, hm) {
    var want = Date.parse(day + "T" + hm + ":00Z"), t = want - 3600000;
    for (var i = 0; i < 3; i++) t += want - amsLocal(t);
    return new Date(t).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  }
  function nextDay(iso) { return new Date(Date.parse(iso) + 86400000).toISOString().slice(0, 10); }
  function stopEvent(s) {
    var names = s.items.map(function (it) { return it.name || "item"; });
    var trip = tripTo(s.address);
    var details = s.items.map(function (it) {
      return "• " + (it.name || "item") + (qtyOf(it) > 1 ? " (" + qtyOf(it) + " pieces)" : "") + (it.invoice ? ", invoice " + it.invoice : "") + (it.url ? "\n  " + it.url : "");
    }).join("\n") + "\nPickup window: " + hours(s) + (trip ? "\nDriving: about " + Math.round(trip.km) + " km, " + Math.round(trip.min) + " min" : "");
    var timed = !!(s.date && s.from);
    return {
      title: "Pickup: " + (names.length > 2 ? names.slice(0, 2).join(", ") + " +" + (names.length - 2) : names.join(", ")),
      location: s.address, details: details, timed: timed,
      start: timed ? utcStamp(s.date, s.from) : s.date.replace(/-/g, ""),
      end: timed ? utcStamp(s.date, s.to && s.to > s.from ? s.to : s.from) : nextDay(s.date).replace(/-/g, ""),
      lead: trip ? Math.round(trip.min) + 15 : 45, uid: s.key.replace(/[^a-z0-9]+/gi, "-") + "@auction-deals"
    };
  }
  function icsText(events) {
    var esc = function (v) { return String(v || "").replace(/\\/g, "\\\\").replace(/;/g, "\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n"); };
    var fold = function (line) { var out = []; while (line.length > 74) { out.push(line.slice(0, 74)); line = " " + line.slice(74); } out.push(line); return out.join("\r\n"); };
    var now = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    var lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//auction-deals//pickups//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
    events.forEach(function (e) {
      lines.push("BEGIN:VEVENT", "UID:" + e.uid, "DTSTAMP:" + now,
        e.timed ? "DTSTART:" + e.start : "DTSTART;VALUE=DATE:" + e.start, e.timed ? "DTEND:" + e.end : "DTEND;VALUE=DATE:" + e.end,
        "SUMMARY:" + esc(e.title), "LOCATION:" + esc(e.location), "DESCRIPTION:" + esc(e.details));
      if (e.timed) lines.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + esc("Time to leave: " + e.title), "TRIGGER:-PT" + e.lead + "M", "END:VALARM");
      lines.push("END:VEVENT");
    });
    lines.push("END:VCALENDAR");
    return lines.map(fold).join("\r\n") + "\r\n";
  }
  function downloadIcs(list, name) {
    var url = URL.createObjectURL(new Blob([icsText(list.map(stopEvent))], { type: "text/calendar" }));
    var a = el("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
  }
  function googleCalUrl(s) {
    var e = stopEvent(s);
    return "https://calendar.google.com/calendar/render?" + new URLSearchParams({ action: "TEMPLATE", text: e.title,
      dates: e.start + "/" + e.end, location: e.location || "", details: e.details }).toString();
  }

  /* favorites that closed lately and aren't in your stock yet: one click turns a win into a purchase with its pickup */
  function wonToCheck() {
    var now = Date.now(), linked = {}, notWon = {};
    items().forEach(function (it) { if (it.lotKey) linked[it.lotKey] = 1; });
    ((P.data && P.data.wonChecked) || []).forEach(function (k) { notWon[k] = 1; });
    return (AD.favorites ? AD.favorites() : []).filter(function (f) {
      var t = Date.parse(f.closes);
      return f.key && !linked[f.key] && !notWon[f.key] && isFinite(t) && t < now && now - t < WON_DAYS * 86400000;
    }).sort(function (a, b) { return Date.parse(b.closes) - Date.parse(a.closes); });
  }
  function closedDay(f) { var t = Date.parse(f.closes); return isFinite(t) ? dayFmt.format(new Date(t)) : today(); }
  function wonDraft(f) {
    var lot = (AD.lots ? AD.lots() : []).filter(function (l) { return l.key === f.key; })[0] || {};
    var when = parseWhen(f.pickupWhen || lot.pickupWhen, closedDay(f));
    var address = f.pickup || lot.pickup || lot.location || null;
    return {
      name: f.title || "", category: (AD.categories || []).indexOf(f.item) >= 0 ? f.item : "", bought: closedDay(f), status: "stock", url: f.url, lotKey: f.key,
      source: [f.siteName, f.auction].filter(Boolean).join(" · "),
      pickup: { date: when.date || null, from: when.from || null, to: when.to || null, address: address, done: false, todo: true },
      _hint: "Fill in what you paid, all in (it's on your invoice)." + (f.bid ? " The last bid the scan saw was " + money2(f.bid) + "." : "") +
        (when.date && address ? "" : " The pickup " + (address ? "day wasn't" : when.date ? "address wasn't" : "day and address weren't") + " in the lot info: check the auction page or your invoice.")
    };
  }
  function hideWon(list) {  /* not won, or already in your stock: don't ask again (on any device) */
    var keys = list.map(function (f) { return f.key; });
    change(list.length === 1 ? "Checked: " + (list[0].title || list[0].key) : "Checked " + list.length + " closed favorites", function (data) {
      data.wonChecked = (data.wonChecked || []).filter(function (k) { return keys.indexOf(k) < 0; }).concat(keys).slice(-500);
    }).then(function () { toast(list.length === 1 ? "Hidden here. It stays in your favorites." : "List cleared. They stay in your favorites."); }, function () {});
  }
  function collect(s, on) {
    var ids = s.items.map(function (it) { return it.id; });
    change((on ? "Collected at " : "Not collected yet at ") + (s.address || s.date), function (data) {
      data.items.forEach(function (x) {
        if (ids.indexOf(x.id) < 0 || !x.pickup) return;
        x.pickup.done = on;
        if (on) x.pickup.doneOn = today(); else delete x.pickup.doneOn;
      });
    }).then(function () {
      toast(on ? "Collected. It's in your stock, ready for an ad." : "Back on your pickup list.");
    }, function () {});
  }

  function pickupView(box) {
    var todo = stops(false, true), done = stops(true, true), won = wonToCheck(), t = today();
    var planned = todo.filter(function (s) { return s.date; });
    var next = planned.filter(function (s) { return s.date >= t; })[0];
    var late = planned.filter(function (s) { return s.date < t; });
    var wk = weekNoOf(t);
    box.append(tiles([
      { label: "To collect", value: String(todo.reduce(function (n, s) { return n + s.items.length; }, 0)), note: todo.length + (todo.length === 1 ? " address" : " addresses"), primary: true },
      { label: "Next pickup", value: next ? cap(dayLabel(next.date)) : "–", note: next ? (next.from ? hours(next) + " · " : "") + (next.address || "address not set") : "nothing planned" },
      { label: "This week", value: String(planned.filter(function (s) { return s.date >= t && weekNoOf(s.date) === wk; }).length), note: "pickups until Sunday" },
      { label: "Won? Check", value: String(won.length), note: "favorites that closed in the last " + WON_DAYS + " days" }
    ]));

    if (won.length) {
      var wp = el("section", { "class": "panel pday" }, el("div", { "class": "ppanel-head pday-head" }, el("h2", { text: "Did you win these favorites?" }),
        el("span", { "class": "spacer" }),
        won.length > 1 ? el("button", { type: "button", "class": "btn-link small", text: "Hide all " + won.length, onclick: function () { hideWon(won); } }) : null,
        el("p", { "class": "small", style: "flex-basis:100%", text: "They closed lately. “I won it” adds the lot to your stock with the pickup address and time filled in; you add what you paid. “Hide” if you didn't win it or it's already in your stock." })));
      won.forEach(function (f) {
        var u = safeUrl(f.url);
        wp.append(el("div", { "class": "pwon" },
          el("div", null,
            u ? el("a", { "class": "title", href: u, target: "_blank", rel: "noopener", text: f.title || f.key }) : el("span", { "class": "title", text: f.title || f.key }),
            el("div", { "class": "meta small" }, f.siteName ? el("span", { text: f.siteName }) : null,
              el("span", { text: "closed " + showDay(closedDay(f)) }),
              f.bid ? el("span", { text: "last bid seen " + money2(f.bid) }) : null,
              f.maxBid != null ? el("span", { text: "your max " + money2(f.maxBid) }) : null,
              f.pickup ? el("span", { text: "📍 " + f.pickup }) : null)),
          el("div", { "class": "c-actions" },
            el("button", { type: "button", "class": "pbtn primary", text: "I won it", onclick: function () { editItem(null, null, wonDraft(f)); } }),
            el("button", { type: "button", "class": "pbtn", text: "Hide", title: "Not won, or already in your stock", onclick: function () { hideWon([f]); } }))));
      });
      box.append(wp);
    }

    box.append(el("section", { "class": "panel list pday" }, subtabs([
      { id: "todo", label: "To collect", count: todo.length },
      { id: "done", label: "Collected", count: done.length }
    ], "pickup")));
    var showDone = pui.pickup === "done";
    var list = showDone ? done.slice().reverse() : todo;
    if (!list.length) {
      box.lastChild.append(el("p", { "class": "empty" }, showDone ? "Nothing collected yet." :
        "Nothing to collect. When you win a lot, add it with “+ Add purchase” (or “I won it” above) and fill in the pickup day and address."));
      return;
    }
    var days = [], byDay = {};
    list.forEach(function (s) { var k = s.date || ""; if (!byDay[k]) { byDay[k] = []; days.push(k); } byDay[k].push(s); });
    if (!showDone && late.length) {
      box.append(el("p", { "class": "small", style: "margin:12px 2px 0", text: late.length + (late.length === 1 ? " pickup is" : " pickups are") + " in the past. Collected? Tick it off, or change the day." }));
    }
    days.forEach(function (d) { box.append(dayPanel(d, byDay[d], showDone)); });
  }
  function weekNoOf(iso) { return Math.floor((Date.parse(iso) / 86400000 + 3) / 7); }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  function dayPanel(d, list, done) {
    var head = el("div", { "class": "pday-head" },
      el("h2", { text: !d ? "No day set yet" : /^to/.test(dayLabel(d)) ? cap(dayLabel(d)) + " · " + weekdayFmt.format(new Date(Date.parse(d))) : dayLabel(d) }),
      d && !done && d < today() ? el("span", { "class": "late", text: ago(d) }) : d && !done && -daysSince(d) > 1 ? el("span", { "class": "small", text: "in " + -daysSince(d) + " days" }) : null,
      el("span", { "class": "spacer" }),
      !done && d && list.some(function (s) { return s.address; }) ? el("a", { "class": "btn-link small", href: routeUrl(list), target: "_blank", rel: "noopener", text: list.length > 1 ? "Route past all " + list.length + " ↗" : "Route ↗" }) : null,
      !done && d ? el("button", { type: "button", "class": "btn-link small", text: list.length > 1 ? "📅 Add the day to your calendar (.ics)" : "📅 Add to calendar (.ics)", onclick: function () { downloadIcs(list, "pickups-" + d + ".ics"); } }) : null);
    var panel = el("section", { "class": "panel pday" }, head);
    list.forEach(function (s) { panel.append(stopRow(s, done)); });
    return panel;
  }
  function stopRow(s, done) {
    var trip = tripTo(s.address);
    return el("article", { "class": "pstop" + (done ? " done" : "") },
      el("div", null, el("div", { "class": "ptime", text: s.date ? hours(s) : "–" }),
        trip ? el("div", { "class": "small", text: "🚗 " + Math.round(trip.km) + " km · " + Math.round(trip.min) + " min" }) : null),
      el("div", null,
        s.address ? el("a", { "class": "paddr", href: mapsUrl(s.address), target: "_blank", rel: "noopener", text: "📍 " + s.address }) : el("span", { "class": "small soon", text: "Address not set" }),
        el("ul", null, s.items.map(function (it) {
          var u = it.url ? safeUrl(it.url) : null;
          return el("li", null,
            el("button", { type: "button", "class": "title plain", text: it.name || "(no name)", onclick: function () { editItem(it); } }),
            qtyOf(it) > 1 ? el("span", { "class": "small", text: qtyOf(it) + " pieces" }) : null,
            it.paid ? el("span", { "class": "small", text: money2(it.paid) }) : el("span", { "class": "small soon", text: "price paid not filled in" }),
            invoiceLink(it), u ? el("a", { "class": "small", href: u, target: "_blank", rel: "noopener", text: "lot ↗" }) : null,
            done && it.pickup.doneOn ? el("span", { "class": "small", text: "collected " + showDay(it.pickup.doneOn) }) : null);
        }))),
      el("div", { "class": "c-actions" },
        done ? el("button", { type: "button", "class": "pbtn", text: "Undo", onclick: function () { collect(s, false); } }) :
          el("button", { type: "button", "class": "pbtn primary", text: "✓ Collected", onclick: function () { collect(s, true); } }),
        !done && s.date ? el("a", { "class": "pbtn", href: googleCalUrl(s), target: "_blank", rel: "noopener", text: "Google Calendar" }) : null));
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

  function editItem(it, focus, draft) {
    var isNew = !it;
    it = it ? JSON.parse(JSON.stringify(it)) : Object.assign({ id: newId(), bought: today(), qty: 1, status: "stock", photos: [], listings: [], sales: [] }, draft || {});
    var pk = it.pickup || {};
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
      photos: el("input", { type: "file", accept: "image/*", multiple: true }),
      pDate: el("input", { type: "date", value: validDay(pk.date) ? pk.date : "" }),
      pFrom: el("input", { type: "time", value: pk.from || "" }),
      pTo: el("input", { type: "time", value: pk.to || "" }),
      pAddr: el("input", { type: "text", value: pk.address || "", placeholder: "Street and number, town" }),
      pDone: el("input", { type: "checkbox" })
    };
    inp.pDone.checked = !!pk.done;
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
      el("h2", { text: isNew ? (it.lotKey ? "You won: add it to your stock" : "Add a purchase") : "Edit " + (it.name || "item") }),
      it._hint ? el("p", { "class": "small", text: it._hint }) : null,
      el("div", { "class": "pgrid" },
        field("What is it?", inp.name), field("Category", inp.category),
        field("Bought on", inp.bought), field("Paid, all in (€)", inp.paid, "Bid + buyer's premium + VAT + costs"),
        field("Pieces", inp.qty, "For a lot: the price is split over the pieces"), field("Status", inp.status),
        field("Where from", inp.source), field("Invoice number", inp.invoice),
        el("div", { "class": "pfield" }, el("label", { text: "Invoice (PDF)" }), invNow, inp.invoiceFile),
        field("New price (€)", inp.newPrice, "For “saved” in Finance")),
      field("Notes", inp.notes),
      el("h3", { text: "Pickup" }),
      el("div", { "class": "pgrid" }, field("Pickup day", inp.pDate), field("Address", inp.pAddr), field("From", inp.pFrom), field("Until", inp.pTo)),
      el("label", { "class": "check" }, inp.pDone, "Collected"),
      el("h3", { text: "Ad" }),
      !isNew && canSplit(it) ? el("p", { "class": "small psplit" }, "This is a lot of " + qtyOf(it) + " pieces, so this is one ad for all of them. For an ad per piece: ",
        el("button", { type: "button", "class": "btn-link", text: "split it into separate items", onclick: function () { closeDlg(); splitItem(find(P.data, it.id) || it); } }), ".") : null,
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
        var pick = { date: validDay(inp.pDate.value) ? inp.pDate.value : null, from: inp.pFrom.value || null, to: inp.pTo.value || null,
                     address: inp.pAddr.value.trim() || null, done: inp.pDone.checked };
        if (pick.done && !pk.done) pick.doneOn = today(); else if (pk.doneOn && pick.done) pick.doneOn = pk.doneOn;
        if (pk.todo && !pick.date && !pick.address) pick.todo = true;  /* won, pickup still to plan */
        values.pickup = pick.date || pick.address || pick.from || pick.to || pick.todo ? pick : null;
        if (it.url) values.url = it.url;
        if (it.lotKey) values.lotKey = it.lotKey;
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
          toast(isNew ? (values.pickup && !values.pickup.done ? "Added to your stock and your pickups." : "Added to your stock.") : "Saved.");
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

  /* a lot of several pieces becomes one item per piece (or per set), each with its own ad, photos and sale.
     The price paid is split over them; they keep the invoice, pickup and where it came from. */
  function canSplit(it) { return qtyOf(it) > 1 && !sales(it).length && statusOf(it) !== "sold"; }
  function splitItem(it) {
    var n = qtyOf(it), names = Array.isArray(it.pieceNames) && it.pieceNames.length ? it.pieceNames : null;
    var rows = [], list = el("div", { "class": "psplit-rows" }), total = el("p", { "class": "small" });
    /* pieceNames: a name per row, or { name, qty } for a set such as a suit (jacket + trousers) */
    var pieces = names ? names.reduce(function (a, x) { return a + ((x && x.qty) || 1); }, 0) : n;
    var each = Math.round((it.paid || 0) / pieces * 100) / 100;
    function addRow(name, pieces, cost) {
      var r = {
        name: el("input", { type: "text", value: name, "aria-label": "Name", required: true }),
        pieces: el("input", { type: "number", min: "1", step: "1", value: String(pieces || 1), "aria-label": "Pieces", title: "Pieces in this item, e.g. 2 for jacket + trousers" }),
        cost: el("input", { type: "text", inputmode: "decimal", value: (cost || 0).toFixed(2).replace(".", ","), "aria-label": "Cost share (€)" })
      };
      r.row = el("div", { "class": "psplit-row" }, r.name, r.pieces, r.cost,
        el("button", { type: "button", "class": "pph-x", "aria-label": "Remove row", text: "✕", onclick: function () { rows = rows.filter(function (x) { return x !== r; }); r.row.remove(); sum(); } }));
      r.cost.addEventListener("input", sum);
      rows.push(r); list.append(r.row);
    }
    function sum() {
      var t = rows.reduce(function (a, r) { return a + (amount(r.cost.value) || 0); }, 0);
      total.textContent = rows.length + (rows.length === 1 ? " item" : " items") + ", costs together " + money2(t) + " of " + money2(it.paid || 0) +
        (Math.abs(t - (it.paid || 0)) > 0.01 ? ": they'll be scaled to add up to what you paid." : ".");
    }
    (names || Array.apply(null, Array(n)).map(function (_, i) { return (it.name || "Piece") + " (" + (i + 1) + "/" + n + ")"; }))
      .forEach(function (nm) { var q = (nm && nm.qty) || 1; addRow(typeof nm === "string" ? nm : nm.name, q, each * q); });
    sum();
    var err = el("p", { "class": "error", hidden: true });
    var form = el("form", { method: "dialog" },
      el("h2", { text: "Split: " + (it.name || "lot") }),
      el("p", { "class": "small", text: "Each row becomes its own item, with its own ad, photos and sale. They keep the invoice" + (it.pickup ? ", pickup" : "") +
        " and where it came from. Selling a suit as one? Make it one row with 2 pieces and delete the other row." +
        (names ? " The names come from the invoice; check the labels." + (names.some(function (x) { return x && x.qty > 1; }) ? " Rows with 2 pieces are already set up as a suit." : "") : "") }),
      el("div", { "class": "psplit-row head", "aria-hidden": "true" }, el("span", { text: "Name" }), el("span", { text: "Pieces" }), el("span", { text: "Cost €" }), el("span")),
      list,
      el("button", { type: "button", "class": "btn-link", text: "+ Add a row", onclick: function () { addRow((it.name || "Piece") + " (" + (rows.length + 1) + ")", 1, 0); sum(); } }),
      total, err,
      el("div", { "class": "buttons" }, el("span", { "class": "spacer" }), el("button", { type: "button", text: "Cancel", onclick: closeDlg }),
        el("button", { type: "submit", "class": "primary", text: "Split" })));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var parts = rows.map(function (r) { return { name: r.name.value.trim(), qty: Math.max(1, parseInt(r.pieces.value, 10) || 1), cost: amount(r.cost.value) || 0 }; });
      if (parts.length < 2) { err.textContent = "Keep at least two rows, or close this and leave it as one lot."; err.hidden = false; return; }
      if (parts.some(function (x) { return !x.name; })) { err.textContent = "Every row needs a name."; err.hidden = false; return; }
      var t = parts.reduce(function (a, x) { return a + x.cost; }, 0), paid = it.paid || 0;
      parts.forEach(function (x) { x.cost = t > 0 ? x.cost * paid / t : paid / parts.length; });
      var cents = parts.map(function (x) { return Math.round(x.cost * 100); });  /* round to cents and keep the exact total */
      cents[cents.length - 1] += Math.round(paid * 100) - cents.reduce(function (a, c) { return a + c; }, 0);
      closeDlg();
      change("Split " + (it.name || it.id) + " into " + parts.length + " items", function (data) {
        var x = find(data, it.id); if (!x || !canSplit(x)) return false;
        var at = data.items.indexOf(x), stamp = Date.now().toString(36);
        var made = parts.map(function (part, i) {
          var y = JSON.parse(JSON.stringify(x));
          ["pieceNames", "soldCost", "listings", "sales"].forEach(function (k) { delete y[k]; });
          y.id = "s" + stamp + "-" + i; y.name = part.name; y.qty = part.qty; y.paid = cents[i] / 100;
          y.newPrice = x.newPrice != null && paid > 0 ? Math.round(x.newPrice * cents[i] / Math.round(paid * 100) * 100) / 100 : x.newPrice;
          y.lotOf = { id: x.id, name: x.name };
          y.photos = i === 0 ? (x.photos || []) : [];
          y.listings = []; y.sales = [];
          y.ad = { title: part.name, text: (x.ad && x.ad.text) || "", price: null };
          y.added = new Date().toISOString();
          return y;
        });
        data.items.splice.apply(data.items, [at, 1].concat(made));
      }).then(function () { toast("Split into " + parts.length + " items. Each has its own ad now."); }, function () {});
    });
    openDlg(form);
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
