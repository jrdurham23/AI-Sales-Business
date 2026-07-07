# Arxon Solutions — Lead-to-Launch Operating Playbook

One command drives the whole day:

```
python main.py today
```

It surfaces, in priority order: hot leads (reply within 4 hours), follow-ups
due, stalled projects to nudge, builds in progress, and new leads to qualify.
Everything below explains how items get onto that list and what to do with
them. All state lives in one SQLite file (`leads.db`), so nothing depends on
memory, spreadsheets, or sticky notes.

## Pipeline at a glance

```
generate → NEW → QUALIFIED → CONTACTED → FOLLOW_UP ─┬→ INTERESTED → CLIENT
                                                    ├→ NOT_INTERESTED
                                                    ├→ SUPPRESSED (opt-out)
                                                    └→ DEAD (4 touches, silence)

CLIENT project: INTAKE → PAYMENT_PENDING → BUILDING → REVIEW → APPROVED → LIVE → MAINTENANCE
```

## 1. Generate leads (weekly batch, ~30 min)

```
python main.py generate --zip 31401
```

Pulls businesses with no website or an outdated one, filters franchises and
duplicates, and writes them as `NEW`. Run one ZIP/county at a time and finish
working a batch before generating more — a worked list of 50 beats an
untouched list of 500.

## 2. Qualify (daily, first 15 min after hot leads)

For each `NEW` lead, spend ≤2 minutes: does the business look real, local,
independent, and reachable? Find an email if you can (their Facebook page or
Google listing usually has one).

```
python main.py leads set 12 --status QUALIFIED --email owner@biz.com --contact "Maria"
python main.py leads set 13 --status DEAD --note "Permanently closed"
```

Qualification criteria: physically open, independently owned, has a phone or
email, and its category is one you have a template/portfolio piece for.

## 3. Outreach (the 4-touch sequence)

**Email is the default cold channel.** Cold SMS requires prior express written
consent under the TCPA and cold calls need a DNC check — the tool warns/blocks
accordingly (see `legal/outreach-compliance.md`). Before sending a new email
draft, check it:

```
python main.py outreach check-email draft.txt
```

Log every touch — this is what schedules the next one automatically:

```
python main.py outreach log 12 --channel email --summary "Intro: no-website angle"
```

Cadence (enforced by the tool): touch 2 at day 3, touch 3 at day 7, touch 4 at
day 14. After touch 4 with no reply the lead is auto-marked `DEAD` — no
manual bookkeeping, no zombie leads. Suggested angles per touch:

1. **Intro** — the specific gap you found ("no website" / "site looks broken on
   phones") plus one concrete benefit.
2. **Softer nudge** — shorter, one question ("Worth a quick call this week?").
3. **Social proof** — a competitor in the same ZIP with a site, or a recent
   delivery of yours.
4. **Low-pressure close** — "Last note from me — door's open if this becomes a
   priority."

When a reply comes in:

```
python main.py outreach reply 12 --outcome interested --summary "Asked for pricing"
python main.py outreach reply 14 --outcome unsubscribe --summary "Said stop"
```

- `interested` → status `INTERESTED`, flagged HOT with a same-day action.
  **The 4-hour reply SLA is the single highest-leverage rule in this playbook** —
  hot leads go cold in days.
- `unsubscribe` → email and phone are written to the suppression list and the
  tool will refuse any future outreach to them. Permanent, by design.

## 4. Convert: interested → client

```
python main.py project create 12
```

Then, in order, before any build work:

1. **Send the services agreement** (`legal/client-services-agreement.md`,
   filled in) and get it signed.
2. **Collect the deposit** (50% recommended). Move the project to
   `PAYMENT_PENDING` until it clears. **Never start building unpaid** — this
   is the payment gate.
3. **Send the intake checklist** (below). When it's back, you have everything
   needed to build without further back-and-forth.

Intake checklist (send as one email or form):
- Business name, tagline, and 2–3 sentences about the business
- Services/products list, service area
- Phone, email, address, hours as they should appear on the site
- Logo + 5–10 photos (or note that stock photos are fine)
- 2–3 websites they like
- Primary call to action (call / book / order / directions)
- Preferred domain name, if any
- Any existing Google Business / Facebook pages to link

## 5. Build → review → live

```
python main.py project set 3 --status BUILDING
python main.py project set 3 --status REVIEW --staging https://client.staging.example.com
python main.py project set 3 --status APPROVED
python main.py project set 3 --status LIVE --production https://clientdomain.com
```

- Build from a per-category starter template, not from scratch (see
  `docs/INFRASTRUCTURE.md`). Target: **first staging link within 5 business
  days of intake**.
- Every client site ships with a privacy policy and terms page
  (`legal/privacy-policy-template.md`, `legal/website-terms-of-use-template.md`)
  — required if the site has a contact form, and it always does.
- **Two revision rounds included**, tracked via `--revisions`. A third request
  is a paid change order (this is in the services agreement — point to it
  kindly).
- Collect the final payment at `APPROVED`, before DNS cutover.
- Projects sitting in `INTAKE`, `PAYMENT_PENDING`, or `REVIEW` for 5+ days
  show up in `today` as stalled — send a nudge; after two ignored nudges mark
  `STALLED` and move on.

## 6. After launch

- Offer the care plan (hosting, edits, backups — recurring revenue) and move
  takers to `MAINTENANCE`.
- Ask for a Google review and a referral at the 2-week check-in — referred
  leads convert far better than cold ones and cost nothing.

## Weekly numbers worth watching

Generated → qualified → contacted → replied → interested → paid. If replies
are low, fix the email angle before generating more leads; if interested-but-
not-paid is high, fix the proposal/deposit step. Volume is never the first
fix.
