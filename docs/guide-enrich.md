# Bulk Auto-enrich (`python -m posrat enrich`)

Hromadný nástroj, který projede celý exam SQLite, pošle každou otázku
přes admin Auto-enrich prompt do AWS Bedrock (s MCP nástroji), a
zapíše vygenerované Explanation/Reference zpátky na disk. Doplněk k
Designer **Auto-enrich** tlačítku, který řeší jednu otázku po druhé
přes UI - tenhle CLI nástroj je pro situace "naimportoval jsem 200
otázek, chci je všechny naráz obohatit".

## Předpoklady

1. **Admin AI settings vyplněné** v `/admin` -> AI chat:
   - Enabled: zaškrtnuto
   - Bedrock model id + region
   - MCP servers JSON (typicky `aws-knowledge-mcp`)
   - Auto-enrich prompt (default template stačí)
2. **AWS credentials** dostupné pro boto3 default chain
   (`AWS_PROFILE` / env vars / IMDS / SSO cache).
3. **Exam SQLite** existuje v `POSRAT_DATA_DIR`. Hotspot otázky se
   automaticky přeskočí.

## Použití

```text
python -m posrat enrich path/to/exam.sqlite [OPTIONS]
```

### Volby

- `--dry-run` - **nic nezapíše na disk**. Zavolá Bedrock + MCP, vyhodí
  reply, klasifikuje verdict, vypíše do stdoutu progress + summary.
  Backup se nevytvoří, `--report` se neuloží, žádné DB writes. Pozor:
  Bedrock turn stále stojí peníze.
- `--overwrite` - re-enrichne i otázky, které už mají Explanation v
  novém formátu (heading `## Correct Answer:`). Default: skip.
- `--no-auto-correct` - **nepřepisuj** `is_correct` flagy v `choices`
  tabulce, když AI nesouhlasí s DB. Default: AI vyhrává, flagy se
  přepíšou na AI letters (operátorská preference: "co nejmíň práce").
- `--report PATH` - serializuj všechny výsledky do JSON souboru
  (per-question id, verdict, db_letters, ai_letters, full
  new_explanation, error_message). Užitečné pro CI / batch review.
  V dry-run módu se report **nepíše** (úprava na další iteraci).

### Příklady

**Standardní run** - enrich všeho co ještě nemá nový template,
auto-correct on, backup made:

```bash
python -m posrat enrich data/aif-c01.sqlite
```

**Dry-run preview** - kolik mismatchů ve stávajícím examu, žádné
změny:

```bash
python -m posrat enrich data/aif-c01.sqlite --dry-run
```

**Re-enrich kompletně** - přepiš i ty, co už enriched jsou (např. po
změně template v adminu):

```bash
python -m posrat enrich data/aif-c01.sqlite --overwrite
```

**Konzervativní** - jen Explanation, AI nesmí měnit moje is_correct:

```bash
python -m posrat enrich data/aif-c01.sqlite --no-auto-correct
```

**Pro CI / review** - generuj JSON report:

```bash
python -m posrat enrich data/aif-c01.sqlite --report enrich-report.json
```

## Co se stane per-otázka

1. **Skip hotspot** - markdown template `## Correct Answer:` nedává
   smysl pro multi-step hotspot otázky (verdict `skipped_hotspot`).
2. **Skip already-enriched** - pokud Explanation už začíná `## Correct
   Answer:` a není `--overwrite` (verdict `skipped_already_enriched`).
3. **Vytáhnout community vote** z legacy Explanation (importer-uložené
   "Community vote distribution\nB (98%)\n..." block z RTF/PDF/HTML
   importu). Header se schová z LLM kontextu (nemá ho cargo-cultovat
   do reply); CLI ho na konci reply appendne ve formě jedné věty.
4. **Build prompt** = admin Auto-enrich template + addendum:
   - Sděl modelu, jaká písmena máme my v DB jako správná, ať to
     ověří proti AWS docs.
   - Pokud nesouhlasí, jeho `## Correct Answer:` heading musí
     reflektovat **jeho verdict**, ne náš.
   - Community vote summary CLI appendne automaticky, ať to model
     do reply nepřidává sám.
5. **Stream Bedrock reply**, parse `## Correct Answer: <X>` heading
   (tolerantní k `BC` / `B, C` / `B and D` / `B, D, E`).
6. **Klasifikace verdiktu**:
   - `match` - DB letters == AI letters (set equality)
   - `mismatch` - DB letters != AI letters
   - `unknown` - AI heading nešel parsovat (model ujel ze scriptu)
   - `error` - Bedrock/MCP raisl, prázdná reply, atd.
7. **Append community vote summary** (pokud byla v původní
   Explanation):
   - 80%+: "Community vote: B (98%) overwhelmingly preferred."
   - 50-79% s runner-up: "Community vote: B (62%) leading; A trails
     at 38%."
   - 50-79% sám: "Community vote: B (60%) leading."
   - <50%: "Community vote split: B (45%), A (40%), C (15%)."
8. **Persist** (kromě `--dry-run`):
   - Explanation se přepíše vždy (kromě skipped/error).
   - Při `mismatch` + default `--auto-correct` se `is_correct` v
     `choices` tabulce přepíše na AI letters. Choice text + id se
     **nemění** byte-for-byte.

## Backup

Před prvním zápisem se vytvoří timestamped backup vedle source
souboru:

```text
data/aif-c01.sqlite        <- původní soubor (po runu už enriched)
data/aif-c01.bak-20260519-153045.sqlite   <- backup před runem
```

Timestamp je UTC. Operátorská preference: zachováváme historii
**všech** runů, ne jen poslední, takže opakované re-enrichy nemažou
předchozí backupy. Pro revert:

```bash
cp data/aif-c01.bak-20260519-153045.sqlite data/aif-c01.sqlite
```

## Reading the output

Per-otázka řádek do stdoutu:

```text
Q12/187 [match]    q-aws-s3-bucket-policy        AI=B  DB=B
Q13/187 [mismatch] q-aws-iam-cross-account       AI=B  DB=C
Q14/187 [skip-hotspot] q-aws-vpc-flow-hotspot
Q15/187 [skip-done] q-aws-s3-versioning
Q16/187 [error]    q-aws-rds-multi-az      :: ThrottlingException: Too many requests
```

Verdict labels:

- `match` - AI confirmed naše marking
- `mismatch` - AI nesouhlasí, doporuč review v Designeru
- `unknown` - AI heading nečitelný (vzácné, model issue)
- `skip-hotspot` - hotspot otázka, přeskočena
- `skip-done` - už enriched, použij `--overwrite` pokud chceš re-run
- `error` - Bedrock/MCP/parse error

Final summary:

```text
Summary:
  total          : 187
  match          : 178
  mismatch       : 5
  unknown        : 0
  skip-hotspot   : 3
  skip-done      : 0
  error          : 1

Mismatches (review in Designer):
  - q-aws-iam-cross-account: AI=B DB=C
  - q-aws-cloudfront-cache: AI=A DB=D
  ...

Errors:
  - q-aws-rds-multi-az: ThrottlingException: Too many requests
```

## Exit codes

- `0` - clean run (každá otázka buď enriched nebo explicitly skipped)
- `1` - alespoň jedna otázka skončila ve verdiktu `error` (`unknown`
  se nepočítá - to je jen model off-script)
- `2` - argparse / CLI usage error

## Idempotence

Default chování bez `--overwrite` je idempotentní: druhý run nad
stejným souborem skipne všechny otázky, které už mají nový template
(`Q5/187 [skip-done]`). Tj. když run přerušíš (Ctrl-C, výpadek
Bedrock kvóty), bezpečně ho můžeš znovu spustit a dokončí se to, co
zbývá. Co se enrichne v jednom runu, neenrichne se podruhé.

## Co je záměrně neimplementované

- **Cost preflight** ("~$X.XX za enrichment 187 otázek?"). Backlog -
  vyžaduje Bedrock pricing API integraci.
- **Concurrency** - sekvenční smyčka, žádný paralelismus. Bedrock RPS
  limity + cost-over-latency priorita = parallel jen pokud bude
  reálně potřeba.
- **Auto-rollback při errors** - backup je manuální revert. Pokud
  enrich selže uprostřed batche, soubor je v půlce upravený a backup
  je k dispozici jako "vrať se zpátky".
- **Per-question retry** - každá otázka má jeden pokus; error -> log
  a pokračuje na další. Re-run skriptu zopakuje skipnuté errors
  (skip-done je jen pro úspěšné).
- **Hotspot enrichment** - skip default. Hotspot otázky potřebují
  vlastní template (multi-step, options pool, žádný jediný `## Correct
  Answer:`) - backlog.

## Související

- **Designer Auto-enrich tlačítko** (per-question UI) - `chat_dialog.py`
  v Designeru, totéž ale pro jednu otázku. Sdílí `effective_enrich_prompt`
  z `AISettings` (admin tab).
- **Memory bank** - `memory-bank/activeContext.md` "Bulk Auto-enrich
  CLI (2026-05-19)" sekce má architekturu (4 nové moduly + CLI hook),
  testovací coverage (75 testů), záměrně neimplementované backlog
  položky.
