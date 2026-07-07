# Infrastructure Guide

Pragmatic setup for running Arxon Solutions and hosting client sites. The
README's Axiom framework describes the fully-automated end state (Coolify on
a Hetzner VPS, self-hosted analytics, agent pipeline); this document is the
lean version to operate **now**, chosen so each piece upgrades into that end
state without rework.

## Principles

1. **Static sites by default.** Local-business sites need no backend. Static
   means near-zero hosting cost, no security patching, and trivial migration.
2. **Client owns the domain; you control the hosting.** Register domains in
   the client's name (or transfer on request) — holding a client's domain
   hostage is a reputation killer and a legal gray zone. Hosting on your
   account is the recurring-revenue hook.
3. **Everything recoverable from git.** Every client site is a repo. Losing a
   server must never mean losing a site.

## Stack

| Concern | Start with | Upgrade at scale |
|---|---|---|
| Site builds | Astro + Tailwind starter templates (one per category: trades, restaurant, salon, auto, retail) | Same — grow the template library |
| Hosting | Cloudflare Pages / Netlify free tier, one project per client | Hetzner VPS + Coolify when sites > ~20 or you want everything under one roof |
| DNS | Cloudflare (free), one account, per-client zones | Same |
| SSL | Automatic on both of the above | Let's Encrypt via Coolify |
| Analytics | Cloudflare Web Analytics (free, no cookie banner needed) | Self-hosted Umami |
| Source control | GitHub — private repo per client site (`client-<name>`) | Same |
| Backups | Git is the backup for sites; nightly copy of `leads.db` to cloud storage | Coolify scheduled backups to object storage |
| Payments | Stripe payment links (deposit + final, per tier) | Stripe API + webhooks (Phase 7.5 of the framework) |
| Forms on client sites | Formspree/Basin free tier, or Cloudflare Workers | Self-hosted Formbricks |

## Email: protect your sending reputation

Cold outreach **will** eventually hurt a domain's reputation. Never send cold
email from the domain clients and prospects visit.

- `arxonsolutions.com` (or your primary) — website, client email, invoices.
- A separate lookalike domain (e.g. `arxonweb.com`) — cold outreach only.
- On both: set up **SPF, DKIM, and DMARC** before the first send (your email
  provider documents the three DNS records; verify with a free checker).
- Warm up a new outreach domain: ≤20 sends/day for the first two weeks, stay
  under ~50/day after. Low volume + personalization also keeps you clearly in
  hand-sent territory rather than bulk-sender territory.

## Domain + DNS runbook (per client)

1. Register the domain (client's name, client's billing if possible).
2. Point nameservers at Cloudflare; add the zone to your account.
3. Deploy the site to staging first (`<client>.pages.dev` or a subdomain of
   your staging domain); send that link for the REVIEW step.
4. On approval + final payment: add the custom domain to the hosting project,
   flip DNS, confirm SSL. Downtime is zero because the site is live on
   staging before cutover.
5. Record the production URL: `python main.py project set <id> --status LIVE
   --production https://...`

## Operational hygiene

- **Secrets:** only in `.env` (gitignored). Never commit API keys; rotate any
  key that ever lands in a repo.
- **`leads.db` is the business.** It holds contacts and the legally-required
  suppression list. Back it up nightly (even `cp` to cloud drive), and treat
  it as personal data under privacy laws — see `legal/README.md`.
- **Access:** password manager + 2FA on registrar, Cloudflare, GitHub, Stripe,
  and email. These five accounts are the whole company.
- **Care plan** (the recurring product): hosting, DNS/SSL management, small
  content edits, uptime monitoring (UptimeRobot free tier), and a monthly
  "your site is healthy" email — that email is what makes the fee feel earned.

## When to move to the VPS/Coolify end state

Trigger any of: >20 hosted sites, a client needs server-side features, or
free-tier limits start costing time. The migration is mechanical because
every site is a static build in git: install Coolify on a Hetzner VPS, point
each repo's deploy at it, flip DNS per site.
