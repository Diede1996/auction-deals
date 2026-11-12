// Opens the built dashboard (site/index.html) in a real browser and checks that it shows lots: no script errors,
// the Lots tab has rows, and lots handed over by the private repository (Troostwijk, Marktplaats bargains) appear
// in their tabs. Used by the daily bot check (scanner/health.py); prints {"problems": [...]} as JSON.
// node scripts/dashboard_check.js site/index.html
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const problems = [];
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("file://" + path.resolve(process.argv[2] || "site/index.html"));
    await page.waitForTimeout(500);
    const count = (id) => page.evaluate((i) => Number((document.getElementById(i) || {}).textContent || 0), id);
    const rows = () => page.evaluate(() => document.querySelectorAll("#rows .row, #rows > article, #rows > div").length);
    const lotsInData = await page.evaluate(() => (window.__AD && window.__AD.lots ? window.__AD.lots().length : -1));
    if (lotsInData < 0) problems.push("the page didn't start (no data).");
    else if (lotsInData > 0 && !(await rows())) problems.push("the page has lots but shows none under Lots.");
    const soon = new Date(Date.now() + 2 * 86400000).toISOString();
    const added = await page.evaluate((closes) => window.__AD && window.__AD.addLots([
      { key: "check:tw", site: "troostwijk", siteName: "Troostwijk", title: "CHECK private auction lot", item: "Laptop", url: "https://example.org", bid: 1, closes: closes },
      { key: "check:mp", site: "mpdeal", siteName: "Marktplaats", title: "CHECK Marktplaats bargain", item: "Laptop", url: "https://example.org", bid: 1, closes: null },
    ]), soon);
    if (added !== 2) problems.push("lots from the private repository can't be added.");
    else {
      if ((await count("count-mp")) < 1) problems.push("a Marktplaats bargain doesn't count in the Marktplaats tab.");
      await page.click("#tab-mp");
      if (!(await page.textContent("#rows")).includes("CHECK Marktplaats bargain")) problems.push("a Marktplaats bargain doesn't show in its tab.");
      await page.click("#tab-all");
      await page.evaluate(() => { const s = document.getElementById("showHidden"); if (s && !s.checked) s.click(); });
      if (!(await page.textContent("#rows")).includes("CHECK private auction lot")) problems.push("a Troostwijk lot from the private repository doesn't show under Lots.");
    }
    errors.forEach((e) => problems.push("script error: " + e.slice(0, 150)));
  } catch (e) {
    problems.push("the browser check broke: " + String(e.message || e).slice(0, 150));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ problems }));
})();
