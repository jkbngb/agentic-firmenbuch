# Stripe-Sample — adoptierte Muster (Referenz, nicht deployen)

Dieser Ordner enthaelt Stripes offizielles Sample "Prebuilt subscriptions"
(Flask `server.py` + `public/`). Es dient nur als **Referenz**. Wir deployen es
**nicht** — unser MCP-Server laeuft auf Starlette/ASGI, nicht Flask.

Der Original-Key in `server.py:14` (`sk_test_…`) wurde beim Ablegen entfernt und
durch `os.environ["STRIPE_SECRET_KEY"]` ersetzt. Keine Secrets im Repo.

## Was wir uebernehmen

1. **Checkout-Session (subscription-mode)** — `create_checkout_session`
   - `mode='subscription'`, `line_items=[{price, quantity:1}]`.
   - Preis wird ueber **`lookup_key`** aufgeloest statt harter Preis-ID
     (`prices.list(lookup_keys=[...])`). Wir setzen `lookup_key = "pro_monthly"`,
     dann ist die Preis-ID nicht im Code verdrahtet.
   - **Erweiterung ggue. Sample:** wir setzen zusaetzlich
     `client_reference_id = <unser Account>` und `customer_email = <Signup-Mail>`,
     damit der Webhook den Kauf eindeutig unserem Account zuordnet (unabhaengig
     davon, mit welcher Mail/Karte bezahlt wird). Trial via
     `subscription_data.trial_period_days = 14`.
   - `success_url` -> unsere Danke-Seite (`willkommen.html`),
     `cancel_url` -> Preisseite.

2. **Customer-Portal-Session** — `customer_portal`
   - `billing_portal.sessions.create(customer=<cus_…>, return_url=…)` fuer
     Self-Service (Kuendigung, Rechnungen).
   - **Unterschied:** das Sample holt die `customer`-ID aus der Checkout-Session.
     Wir speichern die `stripe_customer_id` am Account (beim
     `checkout.session.completed`), sodass das Portal jederzeit ohne alte
     Session-ID geoeffnet werden kann.

3. **Webhook mit Signaturpruefung** — `webhook_received`
   - Kernmuster: `construct_event(payload=<raw body>, sig_header=<Stripe-Signature>,
     secret=<STRIPE_WEBHOOK_SECRET>)`. **Rohen** Request-Body verwenden (nicht
     geparstes JSON), sonst schlaegt die Signatur fehl.
   - `STRIPE_WEBHOOK_SECRET` kommt aus ENV / Key Vault, nie aus Code.
   - Relevante Events: `checkout.session.completed` (-> Plan `pro`),
     `customer.subscription.deleted` und `invoice.payment_failed` (-> sofort `free`,
     keine Grace Period), `customer.subscription.trial_will_end` (optional: Hinweis-Mail).

## Was wir NICHT uebernehmen

- **Flask + statisches `public/`** — unser Stack ist Starlette/ASGI; die Endpunkte
  werden als ASGI-Routen neben dem MCP-Server gebaut, HTML liegt in der SWA-Website.
- **`entitlements.active_entitlement_summary.updated`** — wir modellieren Zugriff
  ueber unser eigenes `plan`-Feld am Account, nicht ueber Stripe-Entitlements.
- **Fehlerbehandlung per `print` / `return e`** — wir loggen strukturiert und geben
  saubere HTTP-Statuscodes zurueck; Webhook ist idempotent (Event-ID entprellt).
- **`YOUR_DOMAIN = localhost`** — kommt bei uns aus der Konfiguration je Umgebung.

## Sicherheit

- Secret-Key + Webhook-Secret ausschliesslich via ENV / Azure Key Vault.
- Alles zuerst im Stripe-**Testmodus**; Live-Umstellung ist ein dokumentierter
  letzter Schritt (siehe `gtm/output/STRIPE_BUILD_PLAN.md`).
