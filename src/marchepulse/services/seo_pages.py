"""Public HTML SEO pages and XML sitemap rendering for organic acquisition."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

from marchepulse.config import Settings
from marchepulse.models import NormalizedTender

_BASE_STYLE = """
:root { color-scheme: dark; --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --muted: #94a3b8; --accent: #38bdf8; --ok: #22c55e; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.6; }
.wrap { max-width: 980px; margin: 0 auto; padding: 2.5rem 1.25rem; }
a { color: var(--accent); }
h1 { font-size: 2.2rem; margin: 0 0 .5rem; }
.lead { color: var(--muted); font-size: 1.1rem; margin-bottom: 1.75rem; }
.cta-row { display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 2rem; }
.btn { display: inline-block; padding: .7rem 1.15rem; border-radius: .5rem; text-decoration: none; font-weight: 600; cursor: pointer; border: none; font-size: .95rem; }
.btn-primary { background: var(--accent); color: #0f172a; }
.btn-secondary { border: 1px solid #475569; color: var(--text); background: transparent; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; margin-top: 1.5rem; }
.card { background: var(--card); border-radius: .75rem; padding: 1.25rem; border: 1px solid #334155; }
.price { font-size: 1.5rem; font-weight: 700; color: var(--accent); margin: .25rem 0 .5rem; }
.badge { display: inline-block; padding: .15rem .5rem; border-radius: 999px; font-size: .8rem; background: #0284c733; color: #7dd3fc; margin-right: .35rem; }
.trial-box { background: var(--card); border: 1px solid #334155; border-radius: .75rem; padding: 1.25rem; margin: 1.5rem 0; }
.trial-form { display: flex; gap: .75rem; flex-wrap: wrap; margin-top: .75rem; }
input[type="email"] { flex: 1; min-width: 220px; padding: .7rem .9rem; border-radius: .5rem; border: 1px solid #475569; background: #020617; color: var(--text); font-size: .95rem; }
pre, code { background: #020617; padding: .2rem .4rem; border-radius: .35rem; font-size: .88rem; overflow-x: auto; }
pre { padding: .85rem; }
.tender-item { background: var(--card); border: 1px solid #334155; border-radius: .75rem; padding: 1rem 1.25rem; margin-bottom: .9rem; }
.meta { color: var(--muted); font-size: .9rem; margin-top: .35rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .9rem; border-top: 1px solid #1e293b; padding-top: 1.25rem; }
"""


def _layout(title: str, description: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(description)}" />
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <main class="wrap">
    {body_html}
    <footer>
      <a href="/">Accueil</a> ·
      <a href="/marches">Appels d'offres recents</a> ·
      <a href="/docs">Documentation API</a> ·
      <a href="/feed.xml">Flux RSS</a> ·
      <a href="/sitemap.xml">Sitemap</a>
      <br />Sources officielles ouvertes : BOAMP (DILA) &amp; API Recherche d'entreprises (DINUM).
    </footer>
  </main>
</body>
</html>"""


def render_landing_html(
    settings: Settings, cached_tenders: int, active_departments: list[str]
) -> str:
    """Render the main B2B landing page with self-service free trial signup."""
    checkout_url = (
        settings.lemonsqueezy_checkout_url
        or settings.stripe_checkout_url
        or "/docs"
    )
    dept_links = "".join(
        f'<a class="badge" href="/marches/departement/{html.escape(d)}">Dept. {html.escape(d)}</a>'
        for d in active_departments[:12]
    )
    dept_section = (
        f'<p style="margin-top:1.5rem">Veille par departement : {dept_links}</p>'
        if dept_links
        else ""
    )

    body = f"""
    <h1>MarchePulse</h1>
    <p class="lead">
      API REST normalisee &amp; alertes automatisees sur les appels d'offres publics francais (BOAMP + enrichissement SIREN officiel).
      Actuellement <strong>{cached_tenders}</strong> avis indexes.
    </p>
    <div class="cta-row">
      <a class="btn btn-primary" href="{html.escape(checkout_url)}">Passer a l'offre Pro</a>
      <a class="btn btn-secondary" href="/marches">Parcourir les marches publics</a>
      <a class="btn btn-secondary" href="/docs">Documentation OpenAPI</a>
    </div>

    <section class="trial-box">
      <h2 style="margin:0 0 .35rem; font-size:1.2rem;">Tester gratuitement maintenant (100 requetes/mois)</h2>
      <p style="margin:0; color:var(--muted); font-size:.95rem;">
        Obtenez une cle API instantanement pour tester les endpoints et configurer vos alertes.
      </p>
      <form class="trial-form" id="trial-form">
        <input type="email" id="trial-email" placeholder="vous@entreprise.fr" required />
        <button class="btn btn-primary" type="submit">Generer ma cle gratuite</button>
      </form>
      <div id="trial-result" style="margin-top:.85rem;"></div>
    </section>

    <section class="grid">
      <article class="card">
        <h3>Starter</h3>
        <p class="price">19 EUR / mois</p>
        <p>2 500 requetes/mois, filtres departement &amp; CPV, enrichissement acheteur, alertes Webhook/Email.</p>
      </article>
      <article class="card">
        <h3>Pro</h3>
        <p class="price">49 EUR / mois</p>
        <p>25 000 requetes/mois, veille multi-criteres illimitee, support prioritaire.</p>
      </article>
      <article class="card">
        <h3>Enterprise</h3>
        <p class="price">149 EUR / mois</p>
        <p>250 000 requetes/mois pour CRM, ESN, cabinets de conseil et plateformes SaaS.</p>
      </article>
    </section>
    {dept_section}
    <script>
      const form = document.getElementById('trial-form');
      const resultBox = document.getElementById('trial-result');
      form.addEventListener('submit', async (e) => {{
        e.preventDefault();
        const email = document.getElementById('trial-email').value.trim();
        resultBox.textContent = 'Creation de la cle en cours...';
        try {{
          const resp = await fetch('/v1/trial', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ email }})
          }});
          const data = await resp.json();
          if (!resp.ok) {{
            resultBox.textContent = data.detail || 'Erreur';
            resultBox.style.color = '#f87171';
            return;
          }}
          resultBox.innerHTML = '';
          resultBox.style.color = '';
          const okMsg = document.createElement('p');
          okMsg.style.color = 'var(--ok)';
          okMsg.textContent = 'Cle creee (copiez-la maintenant) :';
          const preKey = document.createElement('pre');
          const codeKey = document.createElement('code');
          codeKey.textContent = data.api_key;
          preKey.appendChild(codeKey);
          const preCurl = document.createElement('pre');
          const codeCurl = document.createElement('code');
          codeCurl.textContent = 'curl -H "X-API-Key: ' + data.api_key + '" ' + window.location.origin + '/v1/tenders?only_open=true';
          preCurl.appendChild(codeCurl);
          resultBox.append(okMsg, preKey, preCurl);
        }} catch (err) {{
          resultBox.textContent = 'Erreur reseau lors de la creation.';
        }}
      }});
    </script>
    """
    return _layout(
        "MarchePulse — API & Veille Marches Publics BOAMP",
        "API REST normalisee et alertes automatisees sur les appels d'offres publics francais (BOAMP + SIREN).",
        body,
    )


def render_tenders_directory_html(
    tenders: list[NormalizedTender],
    *,
    total: int,
    department: str | None,
    active_departments: list[str],
) -> str:
    """Render public SEO directory page for open public procurement tenders."""
    heading = (
        f"Appels d'offres publics — Departement {department}"
        if department
        else "Appels d'offres publics recents en France"
    )
    dept_pills = "".join(
        f'<a class="badge" href="/marches/departement/{html.escape(d)}">{html.escape(d)}</a>'
        for d in active_departments[:20]
    )
    cards: list[str] = []
    for t in tenders:
        deps = ", ".join(t.departments) or "FR"
        mtypes = ", ".join(t.market_types) or "Public"
        deadline_txt = t.deadline[:10] if t.deadline else "Non precisee"
        cards.append(
            f"""
            <article class="tender-item">
              <div>
                <span class="badge">{html.escape(deps)}</span>
                <span class="badge">{html.escape(mtypes)}</span>
              </div>
              <h2 style="font-size:1.1rem; margin:.4rem 0;">
                <a href="/marches/{html.escape(t.idweb)}">{html.escape(t.title)}</a>
              </h2>
              <div class="meta">
                Acheteur : <strong>{html.escape(t.buyer_name or 'Acheteur public')}</strong> ·
                Publication : {html.escape(t.publication_date or '-')} ·
                Date limite : {html.escape(deadline_txt)}
              </div>
            </article>
            """
        )

    cards_html = (
        "".join(cards)
        if cards
        else "<p>Aucun marche ouvert trouve pour ce filtre actuellement.</p>"
    )
    body = f"""
    <h1>{html.escape(heading)}</h1>
    <p class="lead">{total} avis en base · Recevez ces appels d'offres automatiquement par Webhook ou Email.</p>
    <div class="cta-row">
      <a class="btn btn-primary" href="/">Activer les alertes gratuites</a>
      <a class="btn btn-secondary" href="/marches">Tous les departements</a>
    </div>
    <p>{dept_pills}</p>
    {cards_html}
    """
    return _layout(
        f"{heading} | MarchePulse",
        f"Consultez {total} appels d'offres publics BOAMP filtres et enrichis.",
        body,
    )


def render_tender_detail_html(tender: NormalizedTender, settings: Settings) -> str:
    """Render public SEO detail page for one tender with buyer SIREN info."""
    deps = ", ".join(tender.departments) or "France"
    buyer_block = ""
    if tender.buyer and tender.buyer.siren:
        buyer_block = f"""
        <div class="card" style="margin-top:1rem;">
          <h3 style="margin-top:0;">Profil officiel de l'acheteur</h3>
          <p>
            <strong>{html.escape(tender.buyer.nom_complet or tender.buyer_name)}</strong><br />
            SIREN : <code>{html.escape(tender.buyer.siren)}</code> ·
            Commune : {html.escape(tender.buyer.commune or '-')} ({html.escape(tender.buyer.code_postal or '-')})
          </p>
        </div>
        """

    checkout_url = (
        settings.lemonsqueezy_checkout_url
        or settings.stripe_checkout_url
        or "/"
    )
    body = f"""
    <p><a href="/marches">&larr; Retour a la liste des marches</a></p>
    <h1>{html.escape(tender.title)}</h1>
    <p class="lead">
      Acheteur : <strong>{html.escape(tender.buyer_name or 'Acheteur public')}</strong> ·
      Departement(s) : {html.escape(deps)}
    </p>
    <div class="card">
      <p><strong>Reference BOAMP :</strong> <code>{html.escape(tender.idweb)}</code></p>
      <p><strong>Date de publication :</strong> {html.escape(tender.publication_date or '-')}</p>
      <p><strong>Date limite de reponse :</strong> {html.escape((tender.deadline or '-')[:16])}</p>
      <p><strong>Type de marche :</strong> {html.escape(', '.join(tender.market_types) or '-')}</p>
      <p><strong>Procedure :</strong> {html.escape(tender.procedure or '-')}</p>
      <p><strong>Descripteurs :</strong> {html.escape(', '.join(tender.descriptors) or '-')}</p>
      <div class="cta-row" style="margin-top:1rem; margin-bottom:0;">
        <a class="btn btn-primary" href="{html.escape(tender.source_url)}" rel="noopener nofollow" target="_blank">Voir l'avis officiel BOAMP</a>
        <a class="btn btn-secondary" href="{html.escape(checkout_url)}">Recevoir des alertes similaires</a>
      </div>
    </div>
    {buyer_block}
    """
    return _layout(
        f"{tender.title[:70]} — Marches publics {deps} | MarchePulse",
        f"Appel d'offres public {tender.idweb} par {tender.buyer_name} ({deps}).",
        body,
    )


def build_sitemap_xml(
    base_url: str, departments: list[str], tenders: list[NormalizedTender]
) -> str:
    """Build XML sitemap covering landing, directory, department pages, and recent tenders."""
    root = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    urls = [f"{base_url}/", f"{base_url}/marches"]
    urls.extend(f"{base_url}/marches/departement/{dep}" for dep in departments)
    urls.extend(f"{base_url}/marches/{t.idweb}" for t in tenders)

    for loc in urls:
        url_el = ET.SubElement(root, "url")
        ET.SubElement(url_el, "loc").text = loc

    return ET.tostring(root, encoding="unicode", xml_declaration=True)
