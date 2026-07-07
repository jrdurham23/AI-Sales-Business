# Outreach Compliance Rules

> Not legal advice — see `legal/README.md`. These are the operating rules the
> tooling (`compliance.py`) enforces or warns about, and why.

## Cold email — CAN-SPAM Act

Cold B2B email is legal in the US **if** every message meets all of these:

1. **Truthful headers and subject line** — no misleading "Re:" or fake
   forwards, and the From name is you/your company.
2. **Identified as a commercial message** from a real sender.
3. **Physical postal address** in the message (a registered agent or PO Box
   works).
4. **Clear opt-out mechanism** — "reply unsubscribe" is sufficient — that
   works for at least 30 days after sending.
5. **Opt-outs honored within 10 business days** and never re-contacted. The
   tool honors them instantly: `outreach reply <id> --outcome unsubscribe`
   suppresses the contact's email *and* phone permanently, and
   `outreach log` refuses suppressed contacts with no override.

Use the footer in `compliance.EMAIL_FOOTER_TEMPLATE`, and check drafts with
`python main.py outreach check-email <file>` (verifies opt-out language; the
postal address and subject line are on you).

Penalties are per email, so a "small" batch of non-compliant sends is not a
small risk.

## Cold SMS — TCPA

**Do not cold-text leads.** Texting a number without **prior express written
consent** violates the TCPA at $500–$1,500 *per message*, and business
numbers are not exempt. The tool warns on every SMS attempt to a lead with no
prior relationship. SMS is fine **after** a client relationship exists and
they've agreed to it (put it in the intake).

## Cold calls — TCPA + Do-Not-Call

Manual, human-dialed calls to businesses are generally permitted, with rules:

- **Calling window: 8am–9pm in the recipient's local time.** The tool blocks
  call/SMS logging outside this window (using your machine's clock — mind
  time zones when calling across the country).
- **Check the National DNC registry** (donotcall.gov) before cold-calling.
  Most B2B numbers aren't on it, but sole proprietors' cell numbers often
  are, and those are exactly who this pipeline targets.
- **No robocalls, no auto-dialers, no AI voice, no pre-recorded messages**
  to cold contacts — that requires prior express written consent, full stop.
  This is the legal blocker on the README's "AI Voice Layer" for *cold*
  outreach; keep any future voice work to opted-in clients.

## Scraped/public data — privacy laws

Lead data comes from public sources (OpenStreetMap, Geoapify, search engines),
but "public" does not mean "unregulated":

- **State privacy laws (CCPA/CPRA etc.):** below their revenue/volume
  thresholds most obligations don't apply, but build the habits now — honor
  deletion requests, keep `leads.db` access-controlled and backed up, don't
  sell the data.
- **GDPR:** targeting EU businesses adds a much stricter regime (lawful-basis
  analysis for cold email, right to erasure). Stick to US leads unless you've
  done that homework.
- **Source terms of service:** OSM data is fine with attribution (ODbL);
  scraping search engines violates their ToS — a business risk (blocks) more
  than a legal one, but keep volumes low and polite (the tools already
  rate-limit and back off).

## The one-page version

- Email is the cold channel. Footer with address + opt-out on every send.
- Opt-out = suppressed forever, instantly. The tool enforces this.
- No cold SMS. No robocalls or AI voice to cold contacts. Ever.
- Calls: 8am–9pm recipient time, human-dialed, DNC-checked.
- Treat `leads.db` like the regulated personal-data store it is.
