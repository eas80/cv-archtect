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
   | `STRIPE_SECRET_KEY` | `sk_test_...` (poi `sk_live_...`) |
   | `STRIPE_WEBHOOK_SECRET` | dal passo 3 (si può aggiungere dopo) |
   | `APP_BASE_URL` | l'indirizzo del servizio, es. `https://cv-architect.onrender.com` |
3. Al termine del deploy apri l'indirizzo e **registrati con l'email admin** (`ADMIN_EMAILS`).

Piano Free: il servizio si spegne dopo 15 minuti senza visite e alla visita successiva impiega
circa un minuto a ripartire. Per un uso commerciale conviene il piano Starter (sempre attivo).

## 3. Stripe (webhook)

1. Dashboard Stripe → **Developers → Webhooks → Add endpoint**:
   - URL: `https://<tuo-servizio>.onrender.com/stripe/webhook`
   - Eventi: `checkout.session.completed` e `checkout.session.async_payment_succeeded`
2. Copia il **Signing secret** (`whsec_...`) in `STRIPE_WEBHOOK_SECRET` su Render: il servizio si riavvia da solo.
3. Prova un acquisto con la carta di test `4242 4242 4242 4242`.

Fai tutte le prove in **modalità test** di Stripe; passa alle chiavi live (e a un nuovo endpoint
webhook in modalità live) solo alla fine.

## In locale

```
pip install -r requirements.txt
cp .env.example .env        # e compila le variabili, DATABASE_URL compresa
uvicorn server:app --port 8520
```

Oppure `streamlit run app.py` (senza webhook: i pagamenti vengono verificati direttamente su Stripe
finché la scheda resta aperta).
