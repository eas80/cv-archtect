# Messa online: Supabase + Render

L'app gira su **Render** (app Streamlit e webhook Stripe nello stesso servizio) e salva
i dati su **Supabase** (Postgres). Le tabelle si creano da sole al primo avvio.

## 1. Supabase (database)

1. Crea un progetto su [supabase.com](https://supabase.com), regione **Central EU (Frankfurt)**
   (la stessa del servizio Render). Annota la password del database.
2. Apri **Connect** (in alto) → **Connection string** → scegli **Session pooler** e copia la stringa:
   `postgresql://postgres.<project-ref>:<password>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres`
   - Usa il *Session pooler*: la connessione diretta richiede IPv6, che Render non supporta.
   - Sostituisci `[YOUR-PASSWORD]` con la password del database.
3. Sicurezza: tutte le tabelle hanno **RLS attivo**, quindi la Data API pubblica di Supabase
   (chiavi `anon`/`authenticated`) non vede nulla. Le candidature hanno inoltre una policy per
   utente (`user_id` = utente collegato). Non serve configurare niente a mano.

### Copiare i dati attuali (facoltativo)

Per portare su Supabase utenti, crediti, pagamenti e candidature del vecchio `database.db`:

```
# con DATABASE_URL di Supabase nel file .env
python migrate_sqlite_to_postgres.py
```

Va fatto una volta sola, su un database vuoto (prima di registrare utenti su Supabase).

## 2. Render (app)

1. Su [render.com](https://render.com): **New → Blueprint** e collega il repository GitHub.
   Render legge `render.yaml` e crea il servizio `cv-architect`.
2. Inserisci le variabili richieste:
   | Variabile | Valore |
   |---|---|
   | `DATABASE_URL` | la stringa del Session pooler di Supabase |
   | `ANTHROPIC_API_KEY` | la chiave Anthropic |
   | `STRIPE_SECRET_KEY` | `sk_live_...` in produzione, `sk_test_...` nell'ambiente di prova (vedi passo 4) |
   | `STRIPE_WEBHOOK_SECRET` | dal passo 3 (si può aggiungere dopo) |
   | `BREVO_API_KEY` | API key di Brevo (vedi sotto) |
   | `EMAIL_SENDER` | indirizzo mittente verificato su Brevo |
3. Al termine del deploy apri l'indirizzo e **registrati con l'email admin** (`ADMIN_EMAILS`).

Piano Free: il servizio si spegne dopo 15 minuti senza visite e alla visita successiva impiega
circa un minuto a ripartire. Per un uso commerciale conviene il piano Starter (sempre attivo).

## Email (Brevo)

Servono per la conferma dell'indirizzo alla registrazione e per il reset della password
(codici a 6 cifre). Si usa l'API HTTP di Brevo perché Render, nel piano Free, blocca le porte SMTP.

1. Crea un account gratuito su [brevo.com](https://www.brevo.com) (300 email al giorno).
2. **Senders, domains & dedicated IPs → Senders → Add a sender**: inserisci l'indirizzo mittente
   (anche una Gmail) e confermalo dal link che ricevi. Va in `EMAIL_SENDER`.
3. **SMTP & API → API Keys → Generate a new API key**: va in `BREVO_API_KEY`.

Senza `BREVO_API_KEY` le email non partono e il loro contenuto (codice compreso) viene scritto nel log:
comodo in locale, da evitare in produzione. Gli account creati prima della conferma email risultano già
confermati; `python manage_users.py verified <email> on` conferma a mano un indirizzo.

## 3. Stripe (webhook)

1. Dashboard Stripe → **Developers → Webhooks → Add endpoint**:
   - URL: `https://<tuo-servizio>.onrender.com/stripe/webhook`
   - Eventi: `checkout.session.completed` e `checkout.session.async_payment_succeeded`
2. Copia il **Signing secret** (`whsec_...`) in `STRIPE_WEBHOOK_SECRET` su Render: il servizio si riavvia da solo.
3. Prova un acquisto con la carta di test `4242 4242 4242 4242`.

Chiavi e webhook di Stripe sono separati tra modalità **test** e **live**: il webhook va creato nella
stessa modalità della chiave `STRIPE_SECRET_KEY` del servizio.

## 4. Due ambienti: prova e produzione

`render.yaml` crea due servizi dallo stesso codice:

| Servizio | Ramo git | Stripe | Database |
|---|---|---|---|
| `cv-architect` (produzione) | `main` | chiavi **live** (`sk_live_...`) | Supabase di produzione |
| `cv-architect-test` (prova) | `staging` | chiavi **test** (`sk_test_...`) | Supabase di prova |

Le modifiche si pubblicano prima su `staging`, si provano su `cv-architect-test` con pagamenti
finti e poi si uniscono in `main` per andare in produzione.

L'app riconosce la modalità dalla chiave Stripe:
- con chiavi test mostra in alto la fascia **«🧪 Modalità test»** e accredita solo pagamenti di prova;
- con chiavi live accredita solo pagamenti veri. Un pagamento dell'altra modalità viene ignorato
  (e scritto nel log), anche se un webhook fosse configurato per errore;
- il Pannello admin mostra la modalità attiva; vendite e incasso contano solo i pagamenti di quella modalità.

### Attivare l'ambiente di prova

1. **Supabase:** crea un secondo progetto (es. `cv-architect-test`) e copia la stringa del
   *Session pooler* come al passo 1. Le tabelle si creano al primo avvio.
2. **Render:** apri il Blueprint → **Manual sync** (o attendi la sincronizzazione dopo il push):
   compare il servizio `cv-architect-test`. Inserisci le sue variabili: `DATABASE_URL` del
   progetto di prova, `STRIPE_SECRET_KEY=sk_test_...`, `ANTHROPIC_API_KEY`, `BREVO_API_KEY`, `EMAIL_SENDER`.
3. **Stripe (modalità test):** crea un webhook verso
   `https://cv-architect-test.onrender.com/stripe/webhook` e metti il suo `whsec_...` in
   `STRIPE_WEBHOOK_SECRET` del servizio di prova.
4. Apri l'indirizzo di prova e registrati con l'email admin.

### Passare la produzione alle chiavi live

1. **Stripe:** completa l'attivazione dell'account (dati dell'attività, IBAN, documento).
2. **Stripe (modalità live):** Developers → API keys → copia la **Secret key** `sk_live_...`.
   Poi Webhooks → **Add endpoint** in modalità live verso
   `https://cv-architect.onrender.com/stripe/webhook`, con gli stessi eventi, e copia il nuovo `whsec_...`.
3. **Render, servizio `cv-architect`:** sostituisci `STRIPE_SECRET_KEY` con `sk_live_...` e
   `STRIPE_WEBHOOK_SECRET` con il segreto live. Il servizio si riavvia da solo.
4. Verifica nel Pannello admin la riga «Stripe: modalità live» e che la fascia «Modalità test» non ci sia.
5. Fai un acquisto vero del pacchetto più piccolo e rimborsalo da Stripe (Payments → Refund):
   i crediti di prova si tolgono dal Pannello admin.

I crediti ottenuti in produzione con le vecchie prove di test restano sugli account: correggili dal
Pannello admin (o `python manage_users.py`) se serve.

### Recesso

Prima del pagamento l'utente deve spuntare la casella con cui chiede di usare subito i crediti e
accetta di perdere il diritto di recesso di 14 giorni (contenuti digitali, normativa UE).
Data e ora del consenso restano nei metadati del pagamento su Stripe (`withdrawal_waiver_at`).

## 5. Motori di ricerca (SEO)

`seo.py` serve la pagina iniziale con titolo, descrizione, anteprima social e testo leggibile da
Google, più `/robots.txt` e `/sitemap.xml`. L'ambiente di prova (ramo `staging`) è escluso
dall'indicizzazione in automatico.

1. Su [Google Search Console](https://search.google.com/search-console) aggiungi la proprietà
   «Prefisso URL» con l'indirizzo di produzione e scegli la verifica «Tag HTML»: copia il valore di
   `content="..."` nella variabile `GOOGLE_SITE_VERIFICATION` del servizio `cv-architect` su Render,
   attendi il riavvio e premi **Verifica**.
2. In **Sitemap** invia `sitemap.xml`.
3. Se passi a un dominio tuo, imposta `APP_BASE_URL` su Render: link canonici e sitemap lo usano.

## In locale

```
pip install -r requirements.txt
cp .env.example .env        # e compila le variabili, DATABASE_URL compresa
uvicorn server:app --port 8520
```

Oppure `streamlit run app.py` (senza webhook: i pagamenti vengono verificati direttamente su Stripe
finché la scheda resta aperta).
