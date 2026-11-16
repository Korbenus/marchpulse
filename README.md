# MarchePulse

API REST B2B et moteur d'alertes automatise sur les appels d'offres publics francais, alimente par deux sources officielles gratuites :
- **BOAMP API Explore v2.1** (`https://www.boamp.fr/api/explore/v2.1`)
- **API Recherche d'entreprises DINUM** (`https://recherche-entreprises.api.gouv.fr`)

## Demarrage local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
pytest
uvicorn marchepulse.api.app:app --reload
```

Puis ouvrez :
- Vitrine : `http://127.0.0.1:8000/`
- Documentation interactive (Swagger/OpenAPI) : `http://127.0.0.1:8000/docs`
- Flux RSS public : `http://127.0.0.1:8000/feed.xml`

Executer un cycle complet d'ingestion + enrichissement + alertes + flux SEO :

```powershell
python -m marchepulse.jobs.run_pipeline
```

## Ce que vous devez faire pour passer en 100 % autonome (0 EUR)

1. **GitHub (gratuit)** : creez un depot et poussez le dossier `MarchePulse`.
2. **Base PostgreSQL permanente gratuite (Neon.tech)** : creez un projet gratuit sur Neon et copiez l'URL `postgresql://...` dans `MP_DATABASE_URL` (indispensable sur Render Free pour ne jamais perdre les cles API clients lors des redemarrages).
3. **Render (gratuit)** : connectez votre depot GitHub sur Render avec `deploy/render.yaml` (`plan: free`). Renseignez `MP_DATABASE_URL` et `MP_BASE_URL` (l'URL obtenue, ex. `https://marchepulse-api.onrender.com`), puis notez le `MP_ADMIN_SECRET` genere.
4. **GitHub Actions CRON (gratuit)** : dans les *Secrets* du depot GitHub, ajoutez `MP_BASE_URL` et `MP_ADMIN_SECRET`. Le workflow `.github/workflows/cron_veille.yml` mettra a jour les donnees et enverra les alertes clients toutes les 6 heures automatiquement.
5. **Encaissement (au choix ou les deux)** :
   - **RapidAPI Provider** : importez la spec OpenAPI (`/openapi.json`), activez les plans payants et copiez le `Proxy Secret` dans `MP_RAPIDAPI_PROXY_SECRET`.
   - **LemonSqueezy (ideal en auto-entreprise : TVA & facturation gerees)** ou **Stripe** : creez 3 offres (`Starter 19 EUR`, `Pro 49 EUR`, `Enterprise 149 EUR`), pointez le webhook vers `https://VOTRE_URL/v1/webhooks/lemonsqueezy` (ou `/v1/webhooks/stripe`) et renseignez les variables `MP_*_WEBHOOK_SECRET` et `MP_*_CHECKOUT_URL`.
6. **Emails transactionnels (gratuit jusqu'a 3 000/mois)** : creez un compte **Resend** et renseignez `MP_RESEND_API_KEY` pour que chaque nouveau client recoive automatiquement sa cle API par email apres paiement.
