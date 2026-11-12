# Daily Claude advice per lot

Diede (12 Nov 2026) doesn't trust the bot's Marktplaats matching and wants Claude to do the lookup work, so
Diede only decides to buy yes or no. A scheduled Claude session follows these steps once a day, within
Diede's Claude Pro limit, so keep token use low: read only what the steps say.

Rules: never invent a price from your own knowledge (prices come only from Marktplaats listings); change no
code or other files; send no messages; write the advice in Dutch, short.

## Steps

1. Repositories. `Diede1996/auction-deals` is the working directory. The private one,
   `Diede1996/auction-private`, must sit next to it: `git clone --depth 1 https://github.com/diede1996/auction-private ../auction-private`
   (if it isn't reachable, call `add_repo` for it with push access first). `pip install -r requirements.txt`.
2. `python -m scanner.advice prepare --private ../auction-private --out /tmp/advice-input.json`.
   It prints how many lots need a judgment. With 0 lots and 0 unpriced: stop, there's nothing new.
3. Read `/tmp/advice-input.json`. Per lot you get the title, the bot's search, and its Marktplaats listings
   numbered from 0 (`of` = the price is already per piece of a set; `bid` = "bieden vanaf"). `notShown` = dearer
   listings beyond the 30 shown, which count unless you set `dropUnseen`.
4. Lookups (at most 10 searches): for each lot under `unpriced`, and for a lot whose listings are clearly
   other things (another model, only accessories, professional machines for a home model), make one short
   Marktplaats search. Run them in one go with the GitHub tool `actions_run_trigger` (method `run_workflow`,
   owner `Diede1996`, repo `auction-deals`, workflow_id `advice-lookup.yml`, ref `main`,
   inputs `{"queries": "search one;search two"}`). Then wait for the result in one command:
   `for i in $(seq 1 18); do git -C ../auction-private pull -q; python3 -c "import json,sys; d=json.load(open('../auction-private/data/advice-lookups.json')); sys.exit(0 if d['generated'] > '<time you started, ISO UTC>' else 1)" 2>/dev/null && break; sleep 10; done`
   and read `../auction-private/data/advice-lookups.json` (private sellers only, working, cheapest first).
5. Judge each lot and write `/tmp/advice-judgments.json`, one entry per lot key:
   - `keep`: the numbers of the listings that really are the same thing: same model and version, same
     storage/memory for laptops and phones, same year or chip for Apple, working, the device itself (not only
     a battery, case or part, not a set with other machines). The type number must match exactly (Kärcher
     BR 30/1 is not BR 35/12). Leave out shops and traders: several listings with the same title where only a
     year or number differs ("Pack 2015"), BTW, garantie, factuur, inruil, a company name. When in doubt leave it out.
   - `listings`: instead of `keep`, for a lot priced from your own lookup: `[{"price": 250, "title": "..."}]`
     with only the listings that really compare.
   - `dropUnseen: true` when the search mixes in other models or years, so the unseen dearer ones don't count.
   - `model`: what the lot is, as precisely as the title says. `confidence`: hoog / middel / laag.
   - `verdict`: `kopen` (a good buy if the bid stays under the max), `twijfel` (unclear: type unknown, too few
     listings, mixed lot), `niet` (defect, for parts, poor resale such as Intel MacBooks, or a trap).
     The code turns `kopen` into "te duur" by itself when the next bid is above the max bid.
   - `reason`: one or two sentences in Dutch: what compares, and what the bot got wrong if it did.
     `risks`: optional, short (battery missing, accu age, unknown version).
   - For a lot with `sameListingsAs`: `{"sameAs": "<that key>"}`, plus `model`/`reason` when it differs
     (e.g. "2x").
   - Fewer than 2 good listings gives no price and no max bid: say so in the reason.
6. `python -m scanner.advice apply --private ../auction-private --judgments /tmp/advice-judgments.json`
7. Save: `cd ../auction-private && git add data/claude-advice.json && git commit -m "Claude advice" && git push`
   (on a rejected push: `git pull --rebase` and push again, at most 3 times). The dashboard reads it from there.
8. End with a two-line summary: how many lots judged, and the lots labelled "kopen" with their max bid.
