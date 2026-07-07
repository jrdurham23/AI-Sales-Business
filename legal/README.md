# Legal — Read Me First

> **These documents are working templates, not legal advice.** Have a
> licensed attorney in your state review them before relying on them, and
> re-review when you change what you sell or where you sell it. US-centric;
> other jurisdictions differ.

## What's in this folder

| File | What it's for | When it's used |
|---|---|---|
| `outreach-compliance.md` | The rules for cold email/SMS/calls (CAN-SPAM, TCPA, DNC) and how the tooling enforces them | Every outreach touch |
| `client-services-agreement.md` | Contract template between Arxon and a client | Signed **before** any build work (Workflow §4) |
| `privacy-policy-template.md` | Privacy policy for client websites (and your own) | Shipped with every site that has a contact form — i.e., every site |
| `website-terms-of-use-template.md` | Terms page for client websites | Shipped with every site |

## Business setup checklist (one-time)

- [ ] **Form an LLC** (or equivalent) before signing the first client — it
      separates business liability from personal assets. Cheap and fast in
      most states.
- [ ] **EIN + separate business bank account** — never mix funds; it can
      pierce the LLC's liability protection.
- [ ] **General liability + professional (E&O) insurance** — E&O matters
      because you're delivering professional services; some commercial
      clients will require proof of it anyway.
- [ ] **Attorney review** of `client-services-agreement.md` — one flat-fee
      review, reused for every client.
- [ ] **Sales tax check** — some states tax web design/development services.
      Ask your accountant which of your line items are taxable.
- [ ] **Privacy policy + terms on your own site**, using the two templates.

## Ongoing obligations (built into the workflow)

- **Suppression list is law, not preference.** CAN-SPAM requires honoring
  opt-outs within 10 business days; the tool enforces it instantly and
  refuses future sends. Never bypass it, never delete `suppression_list`
  rows.
- **Lead data is personal data.** Names, emails, and phone numbers in
  `leads.db` — even from public sources — fall under state privacy laws
  (e.g., CCPA/CPRA) once you hit their thresholds. Keep the database backed
  up, access-controlled, and delete data on verified request.
- **Client content is the client's responsibility, in writing.** The services
  agreement warrants that content the client supplies (text, photos, logos)
  doesn't infringe anyone's rights. For content *you* supply, use properly
  licensed sources only (your own photos, licensed stock) and keep records of
  the license.
- **Every commercial email** needs the compliant footer — see
  `outreach-compliance.md`; check drafts with
  `python main.py outreach check-email <file>`.
