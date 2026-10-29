# Responsible use

This code helps you write to people who have not asked to hear from you. That is legal in many
places, and useful when it is done well, but only within rules that protect the people on the
other end. The software enforces some of those rules. The rest are yours.

**This is not legal advice.** The rules depend on where your recipients are and on your own
situation. Check the primary sources below, and ask a lawyer when in doubt.

## What the code enforces

- **Nothing is sent by default.** `DRY_RUN=true` writes messages to a local outbox, and live
  sending also needs `gate selftest` to pass for your provider.
- **A person approves every message by default** (`REVIEW_MODE=true`).
- **Low caps:** one message per person until they reply, three people per company and 50
  messages per sending domain per 24 hours. Raise them slowly, and only while complaint rates
  stay low.
- **Every message says who it is from** (a real name, your company and your postal address)
  and carries a one-click unsubscribe link that works with no login and no confirmation step.
- **Opt-outs stick.** Unsubscribes, complaints, hard bounces and replies such as "remove me" go
  on a suppression list that nothing in the code removes anyone from. People on it are never
  added to the contact database again.
- **Every contact records where the address came from, when, and on what lawful basis.**
  `prospect provenance EMAIL` answers "where did you get my email address?".
- **No role addresses** (`abuse@`, `postmaster@`, ...) and **no consumer mailboxes** (gmail.com
  and similar).
- **No scraping.** There is no code that collects email addresses from web pages, and none for
  LinkedIn. Signals describe companies, never people. The optional web research (off by
  default) uses Claude's own web search and fetch tools to read about companies; the agent has
  no way to add a contact or an address from it, and a recorded signal may not contain an
  email address.

## What is up to you

**Have a lawful basis and be ready to show it.** For people in the EU and the UK, that usually
means legitimate interests (GDPR Article 6(1)(f)), weighed and written down before you start,
and telling people where you got their data (Article 14). `DEFAULT_LAWFUL_BASIS` is empty on
purpose: set it only once you have done that work.

**Only use data you can account for.** Your own customers and contacts, signals you measured
yourself, and licensed data providers used within their terms. No bought or rented lists
without provenance: if you cannot say where an address came from, it does not go in. The CSV
import refuses rows without a source and lawful basis for that reason.

**Do not collect addresses from web pages.** Some addresses are planted in pages, including
page source code, specifically to catch senders who scrape the web or buy scraped lists. They
are spam traps, and mailing one damages your sending reputation.
([Spamhaus](https://www.spamhaus.com/resource-center/spamtraps-fix-the-problem-not-the-symptom/))
Address verification services confirm that a mailbox exists; they cannot reliably tell you
that it is not a trap, and they say nothing about whether the person agreed to hear from you.

**Do not let a data provider send for you.** Tools such as Apollo can add people to sequences
that send email on their own, which bypasses the gate and every check in it. The Apollo
adapter here can only search and enrich.

**Be honest in every message.** Your real name and company, a subject that matches the content,
nothing that pretends to be a reply or forward (the gate refuses a `Re:` or `Fwd:` subject until
the person has replied), and no claims you cannot back up. The agent is told to state only what
the signal it measured shows.

**Read what the agent drafts.** Keep `REVIEW_MODE` on for at least the first two weeks, and
reject drafts that are vague, pushy or wrong.

**Watch your complaint rate.** Google asks senders to keep the spam rate reported in Postmaster
Tools below 0.10% and never to reach 0.30%. Senders of more than 5,000 messages a day to Gmail
accounts must also support one-click unsubscribe (RFC 8058), which this gate adds to every
message. ([Google: email sender guidelines](https://support.google.com/a/answer/81126))

**Answer people.** If someone asks where you got their address, tell them
(`prospect provenance`). If they ask you to delete their data, `prospect forget` erases the
contact and keeps them on the suppression list so they are never added again.

## Primary sources by region

| Where your recipients are | Start here |
|---|---|
| United States | FTC, [CAN-SPAM Act: A Compliance Guide for Business](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business): accurate headers, honest subject lines, a postal address, a clear way to opt out, and opt-outs honoured within 10 business days. |
| European Union | [GDPR, Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj): lawful basis (Article 6) and information when data is not collected from the person (Article 14). National e-privacy rules on marketing email also apply. |
| United Kingdom | ICO, [Electronic mail marketing](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/) (PECR): the consent rules cover individual subscribers, including sole traders and some partnerships; corporate subscribers can be emailed without consent. UK GDPR applies to the personal data either way. |
| Canada | [Canada's Anti-Spam Legislation (S.C. 2010, c. 23)](https://laws-lois.justice.gc.ca/eng/acts/E-1.6/): commercial electronic messages need the recipient's consent, express or implied. |
| India | [The Digital Personal Data Protection Act, 2023](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf) (Gazette of India), and the rules made under it. |

Sources were checked on 29 October 2026. Laws and guidance change; check the current version.
