# Job Radar

Bot gratuito per monitorare opportunità freelance IT e inviarle su Telegram.

## Piano

1. Acquisizione concorrente da API JSON e feed RSS pubblici, con frequenze conformi alle fonti.
2. Normalizzazione, filtri deterministici (incarichi, budget, geografia, scadenza), scoring configurabile e deduplica SQLite.
3. Notifiche Telegram per i nuovi risultati, digest giornaliero e avvisi per errori ripetuti. Gemini è facoltativo.
4. Esecuzione locale, test con fixture offline e workflow Actions ogni 15 minuti.

## Fonti

| Fonte | Endpoint / metodo | Auth | Frequenza / condizioni | Uso |
|---|---|---:|---|---|
| Remotive | `https://remotive.com/api/remote-jobs` JSON | No | Risposta live HTTP 200 (20/10/2026); massimo consigliato 4 richieste/giorno, polling ogni 6 ore. Link e attribuzione mantenuti. | Sì |
| Remote OK | `https://remoteok.com/api` JSON | No | Risposta live HTTP 200 (20/10/2026); API pubblica. Telegram indica “Remote OK” e collega ogni annuncio. | Sì |
| Working Nomads | `https://www.workingnomads.com/api/exposed_jobs/` JSON | No | Risposta live HTTP 200 (20/10/2026); API pubblica verificata. Non ho trovato una pagina ufficiale aggiornata con condizioni d’uso/rate limit specifici per questo feed; frequenza bassa e link diretto. | Sì, condizioni specifiche non documentate |
| Jobicy | `https://jobicy.com/api/jobs` JSON | No | Risposta live HTTP 200 (20/10/2026); API pubblica, parametri `geo`, `industry`, `tag`, `count`; massimo 200 elementi e frequenza moderata. Attribuzione e link Jobicy conservati. | Sì |
| Hacker News | Algolia `https://hn.algolia.com/api/v1/search_by_date` | No | Risposta live HTTP 200 (20/10/2026); query per commenti “Freelancer”, “Who is hiring” e query italiane. | Sì, API di ricerca pubblica |
| Arbeitnow | `https://www.arbeitnow.com/api/job-board-api` JSON | No | Risposta live HTTP 200 (20/10/2026); API pubblica. Molti annunci sono dipendenti: esclusione attraverso regole employment e seniority. | Sì |
| Google Alerts (Italia) | RSS privato inserito in `rss_feeds` | Feed URL | Crea alert per le query italiane qui sotto e imposta consegna RSS. URL segreto: GitHub Secret `GOOGLE_ALERTS_RSS_URL` oppure variabile locale. | Opzionale |

Le verifiche web del 20/10/2026 hanno restituito JSON per Remote OK, Working Nomads e Arbeitnow e documentazione corrente per Remotive e Jobicy. Alcuni URL API non sono renderizzati dal browser di ricerca, ma sono endpoint documentati; conferma l’accessibilità dal tuo runner con `--dry-run`. Le condizioni dei servizi possono cambiare.

Esclusioni: We Work Remotely vieta esplicitamente di memorizzare i propri dati API e vieta servizi di ricerca offerte senza autorizzazione, incompatibile con questa pipeline. Reddit richiede uso conforme ai Data API Terms e non è incluso perché scraping/feed anonimo non costituiscono un metodo stabile autorizzato. Freelancer.com ha API ufficiale, ma i termini del sito vietano l’accesso automatizzato senza permesso scritto e le API Terms limitano conservazione/uso dei dati; non la abilito senza autorizzazione. AddLance e altre piattaforme italiane non espongono un feed/API pubblica verificabile; non vengono sottoposte a scraping. LinkedIn e Upwork non sono fonti.

| Fonte italiana | URL / metodo | ToS/robots e uso |
|---|---|---|
| Google Alerts | [google.com/alerts](https://www.google.com/alerts), feed RSS personale | Feed fornito esplicitamente dall’utente; usato come RSS, senza scraping. Legalmente utilizzabile secondo disponibilità del servizio e i suoi termini. |
| Arbeitnow | [API job board](https://www.arbeitnow.com/api/job-board-api), JSON | API pubblica. Non è un mercato italiano, ma l’endpoint include località da filtrare. |
| Jobicy / Remotive / Remote OK | API pubbliche sopra | Fonti internazionali; i risultati remoti possono includere clienti italiani. Si mantengono attribuzione e link alle inserzioni. |

Query suggerite per Google Alerts: `"cerco sviluppatore sito web"`, `"cerco programmatore freelance"`, `"serve software gestionale"`, `"cerco chi sviluppa automazioni"`, `"preventivo sito e-commerce"`, `"cerco developer per progetto"`. Inserire il link RSS ottenuto in `GOOGLE_ALERTS_RSS_URL`.

## Installazione

Richiede Python 3.12.

```bash
py -3.12 -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --dry-run
pytest
```

## Telegram e GitHub Actions

1. In Telegram apri [@BotFather](https://t.me/BotFather), esegui `/newbot`, conserva il token e avvia una chat col bot.
2. Per ottenere il chat ID, invia un messaggio al bot e visita `https://api.telegram.org/bot<TOKEN>/getUpdates`; usa `message.chat.id`. Non condividere il token.
3. Imposta le variabili locali `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`; per Actions aggiungile in **Settings → Secrets and variables → Actions → New repository secret**.
4. Facoltativo: `GEMINI_API_KEY` (o `LLM_API_KEY`) per il reranking (il sistema funziona senza LLM); `GOOGLE_ALERTS_RSS_URL` per il feed italiano.
5. Abilita Actions nel repository e avvia manualmente `Job Radar` una prima volta. Per il test, avvia `workflow_dispatch` con `send_test=true`; il workflow invia il messaggio di prova. Il workflow usa cache Actions per lo SQLite e mantiene una sola esecuzione alla volta. I runner GitHub standard sono gratuiti e illimitati per repository pubblici; repository privati consumano la quota del piano e possono generare costi oltre tale quota.

Il test Telegram effettivo richiede credenziali che non sono disponibili in questo workspace: esegui `python -m src.main --send-test` dopo averle impostate.

## Configurazione

Modifica `config.yaml`: fonti, query, parole chiave, soglie, pesi, cambi statici USD/GBP, limiti e orario digest. La soglia iniziale è volutamente bassa (`35`). Le parole chiave supportano `*` come suffisso e vengono normalizzate senza distinzione di accenti. Le variazioni plurale/singolare più comuni sono generate automaticamente.

Budget assente è ammesso; budget mensile/annuale non viene trattato come prezzo del progetto. Un importo inferiore alle soglie è scartato solo quando la tipologia è riconoscibile. Le offerte con budget ambiguo rimangono con indicazione “non specificato”.

Per aggiungere una fonte, crea un adapter in `src/adapters/` che restituisca `Job` oppure usa l’adapter RSS generico e registralo in `config.yaml`.

## Comandi

```bash
python -m src.main --dry-run       # non invia messaggi
python -m src.main --send-test    # invia un test Telegram
python -m src.main --once         # un ciclo ordinario
```

## Troubleshooting

- Nessun messaggio: controlla secrets, chat ID, threshold, fonti abilitate e log Actions.
- `getUpdates` vuoto: prima premi Start e manda un messaggio al bot.
- Fonte non raggiungibile: il fallimento isolato viene registrato, ritentato con backoff e segnalato dopo tre run consecutive.
- Duplicati: controlla cache Actions e chiave `state` workflow; elimina lo SQLite solo per resettare la cronologia.
- Gemini: è facoltativo; errori e rate limit disattivano il reranking per il ciclo.

## Passi manuali e idee future

- Creare il bot, aggiungere i secrets e abilitare Actions.
- Facoltativo: creare gli alert RSS italiani e salvare il feed come secret.
- Facoltativo: attivare Gemini free tier e verificare i limiti correnti del piano.
- Possibili miglioramenti: bozze di proposte personalizzate dal CV (mai inviate automaticamente), calibrazione soglie dopo una settimana, feedback 👍/👎 sui risultati.
