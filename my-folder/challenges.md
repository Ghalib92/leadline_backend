# Leadline: challenges and how we solved them

Raw material for LinkedIn posts. Each entry: what went wrong, why, the fix, and the
lesson, with the real numbers. Newest stage first. Added to at the end of every stage.

Context for readers: Leadline is an AI app for Kenyan businesses. It finds client leads
(businesses that need what you sell), government tenders and grants, scores them,
and writes the pitch or the bid. Built with Django, LangGraph, Groq (free tier) with
Gemini as backup, Temporal for background jobs, React Router on the front end.

---

## Stage 10 (18 Nov 2026): stop guessing, go and look

### The fix for "the AI invents facts" was to go and get the fact
- **Recap:** in stage 9, pitches kept saying "I couldn't find your website" about
  businesses nobody had checked. Prompt rules failed; a detector caught each wording
  until the model found a new one.
- **Fix:** when someone approves a lead that was never checked, Leadline now looks it
  up first (one web search, then a visit to the site), saves what it found, and only
  then writes the pitch. It adds 10-20 seconds; the card says "Checking their website,
  then writing the pitch…".
- **Result, live:** Marie Stopes Clinic (Nairobi) had no website on OpenStreetMap. The
  lookup found mariestopes.or.ke, saw online booking and payments but no chat and a
  "© 2023" footer, and the pitch opened with exactly that. For a primary school with no
  site anywhere, "I couldn't find a website" is now simply true.
- **Lesson:** guardrails on the output treat the symptom. Better input removes the
  reason to make things up.

### The AI's advice went stale the moment we learned more
- **What happened:** the first pitch after a lookup still offered the clinic "a booking
  site with M-Pesa payments", which it already had. The idea came from the scoring
  step, made before anyone knew the site existed.
- **Fix:** when a lookup finds the website, the old idea is dropped and the pitch works
  from what the site shows.
- **Lesson:** every AI conclusion has a "decided when" date. New facts should expire
  old conclusions.

### Small UX debts, paid
- Upload ten documents at once (a company's certificates come in a pile). If one fails,
  only the failed ones stay listed, so trying again never uploads a duplicate.
- Leads and tenders now list best match first. "Newest first" meant nothing when a run
  finds 30 things in the same minute.

---

## Stage 9 (18 Nov 2026): using the app like a real customer

### 850 passing tests, and the app still didn't work for a real user
- **What happened:** Every test passed. Then we drove the app in a real (headless)
  browser as a new customer: sign up, onboarding, upload documents, create workers,
  run them, approve a lead and a tender. We found about 15 problems in one afternoon.
- **Examples:** the app wrote sample text instead of real bids (a config flag left on
  "fake"); a worker named "Hotels in Nairobi" searched schools and chemists in
  Mombasa; a "Try again" error card on the first tender we opened.
- **Lesson:** unit tests check that each part does what you told it. Only using the
  product end to end checks that you told it the right things.

### "Hotels in Nairobi" found schools in Mombasa
- **Why:** each worker had a name, but its search came from the company-wide brief
  (every customer type × every city: 6 searches). The name was just a label.
- **Fix:** a worker can narrow to its own kinds of customer and places. The same
  worker then planned 1 search and found 18 Nairobi hotels in 1 minute 43 seconds.
- **Lesson:** if a user can name something, they expect the name to mean something.

### The AI skipped the best source for tenders
- **What happened:** Kenya's tender portal returned 8 matching tenders. The AI
  "researcher" that picks which results to read chose 1 of them, and spent its other
  reads on news articles and tender-aggregator sites that failed or were irrelevant.
- **Why:** portal results (complete, structured, free to read) competed for the same
  reading slots as random web pages.
- **Fix:** results from official portals are all read (up to 12 a round) without
  asking the AI to pick. The AI only picks among web pages.
- **Lesson:** don't ask a model to make a decision that code can make better.

### The pitch said "I noticed you don't have a website", and nobody had looked
- **What happened:** the business directory (OpenStreetMap) listed no website, so the
  AI wrote "I couldn't find a website for your school". We never searched.
- **First fix:** a rule in the prompt. The model ignored it.
- **Second fix:** code that detects the claim and asks for a rewrite. The model found
  new wordings each time: "couldn't find a *public* website", "couldn't *locate* a fee
  payment portal", "I noticed I couldn't find an online portal on your site".
- **What worked for now:** a clear prompt rule plus a wider check, until the pitch
  asked instead: "Do parents enrol or pay fees online today?"
- **Real fix (stage 10):** check the business's website when a lead is approved, so
  the pitch rests on a fact, not a guess.
- **Lesson:** you can't regex your way out of a model inventing facts. Give it the fact.

### Approving a school as a client made the AI keener on a tender
- **What happened:** the tender scorer explained a score with "past decision approved
  a similar education-tech project (Matuga Girls High School)". That was a client lead,
  not a tender.
- **Why:** "decision memory" (similar past approvals and rejections) searched every
  decision the company made, whatever the kind.
- **Fix:** the tender and grant scorers only learn from tender and grant decisions.
- **Lesson:** memory needs boundaries, or the AI learns the wrong lesson.

### The logs blamed the wrong model
- **What happened:** briefs failed with "rate limit" errors from Groq. The real cause:
  Groq was rate-limited, so the backup (Gemini) answered, but Gemini misspelt a field
  name ("meeds" instead of "meets") and the answer was rejected.
- **Why:** LangChain's fallback re-raises the *first* error when the backup also fails.
- **Fix:** a missing field now has a safe default instead of discarding the whole brief.
- **Lesson:** when a fallback fails, read the whole trace, not the last line.

### The AI answered with the question
- **What happened:** asked for a proposal outline as JSON, the open model
  (gpt-oss-120b) returned the JSON *schema*, the description of the format, twice.
  The bid ended with no sections.
- **Fix 1:** the retry now tells the model what went wrong ("you returned the schema
  itself, return the data"), instead of asking the same question again.
- **Fix 2:** if the outline still fails, the bid follows the tender's own list of
  sections, built in code.
- **Lesson:** a retry that repeats the same prompt often repeats the same mistake.

### "Due in 2 days" for a tender closing tomorrow morning
- **Why:** days were counted as hours ÷ 24 and rounded, and deadlines were stored as
  23:59. At 10:00 today, a 10:00 deadline tomorrow showed "today" on one card and
  "2d left" on another.
- **Fix:** count calendar days in Kenya time.
- **Lesson:** "days left" is a calendar question, not an arithmetic one.

### Small things a real user would notice
- Emails said "Hi user19@example.com" (no name on the profile, so it used the email).
- Plain-text emails showed "Kilifi M&amp;E system": Django escapes HTML even in
  text templates.
- A new user had to invent a folder before uploading anything. Now there is one.
- Marketing page still said "Your next grant is already out there", for a product
  now mostly about finding clients.

### New feature from the pass: a Monday summary email
- The best finds of the week, what waits for review, approved bids closing soon with
  the documents still missing, and calls due back. Nothing is sent in a quiet week.

---

## Stage 8 (18 Nov 2026): the bid writer

### Tender documents are 90-170 pages; the model can read about 20
- **What happened:** Kenyan tender documents run to 90-170 pages, mostly standard
  instructions identical in every tender. The free model tier refuses a request over
  8,000 tokens (about 27,000 characters).
- **Fix:** pick pages in code. The parts that differ (the Data Sheet with mandatory
  requirements, the scoring table, the terms of reference) start with a heading near
  the top of a page. A 90-page document came down to 18 pages that held everything.
- **Bug on the way:** PDF text loses its spaces ("Othereligibilityandmandatorycriteria"),
  so matching ignores spaces. Then "PARTICULARS" matched as "PART I", and every file in
  a folder called "storage" looked like a "ToR" (s-*tor*-age).
- **Lesson:** most "AI" problems are text-processing problems first.

### The first AI bid invented a project manager
- **What happened:** the first live bid described a project leader with "12 years of
  experience" who delivered a "Nairobi County Open Data portal worth KES 4 million".
  None of it existed. In a government tender that is a false declaration.
- **Why:** the tender scores "over 10 years' experience" and "two similar projects of
  KES 3M+". The planning step turned the scoring rules into "facts", and the writer
  wrote them up.
- **Fix:** the planner may only state facts from the company's documents; every gap
  becomes a [TODO]; the review step flags claims the documents don't support. With the
  company profile uploaded, the next bid named the real team and the real KES 1.8M
  project.
- **Lesson:** an AI that writes to a scoring rubric will try to score. Make "I don't
  know" an allowed answer.

### "Request too large" was not covered by the backup model
- **What happened:** the backup (Gemini) took over on rate limits (HTTP 429), but Groq
  answers an over-size request with HTTP 413, which the SDK reports like any bad
  request. Bids failed instead of switching.
- **Fix:** a small wrapper turns 413 into "request too large" and sends it to Gemini.
- **Lesson:** "rate limited" and "too big for your plan" are different errors.

### A source had failed on every call since the day it was added
- **What happened:** the EU tenders source (TED) returned HTTP 400 on every search.
- **Why:** it sent plain text where the API needs its own query language, and the
  tests mocked the API in the same wrong format, so they passed.
- **Fix:** build real queries. Surprise on the way: inside the API's text search,
  "OR" doesn't mean "any of"; each word needs its own search term.
- **Lesson:** a mock that agrees with your bug is worse than no test.

### Privacy: tender records include the official's national ID
- Each tender on Kenya's portal comes with the name, national ID number and phone of
  the official who posted it. Leadline never reads or stores those fields; it keeps
  only the buying office's official contacts.

---

## Stages 3-7 (14-17 Nov 2026): finding clients and tenders

### The AI gave every business 100%
- **Why:** a small model asked to score everything (fit, need, reachability) scored
  generously.
- **Fix:** the model scores only fit (0-40). Need (0-35) and reachability (0-25) are
  counted in code from what was observed: no website, a broken site, a phone number.
- **Lesson:** let the model judge what needs judgement; count what can be counted.

### A 5-star hotel was pitched as having a "broken website"
- **Why:** its site timed out once during the check.
- **Fix:** only hard failures count (domain lapsed, connection refused, error pages).
  Timeouts and "access denied" are "couldn't check", not evidence.
- **Lesson:** "I couldn't see it" is not "it isn't there".

### Free maps data is thin in Kenya
- OpenStreetMap listed a phone for 9 of 80 Nairobi hotels. So a business with no
  website on the map is "unknown", never "no website", and Leadline searches the web
  for its site before saying anything.

### "hotels.com" matched "panarihotels.com"
- Recognising directory sites by text matched inside other domains. Domain patterns
  now match on boundaries.

### API keys sat unused for three stages
- Gemini, Jina and Firecrawl keys were in the config but no code read them. Now every
  stage starts by checking each configured key against the code.

### The free tier's daily token cap
- Groq's free tier allows about 200,000 tokens a day per model. Scoring moved to a
  smaller model with its own allowance, and Gemini became the backup when Groq says
  no. A bid costs about 30,000 tokens.

### OpenStreetMap refused the app (HTTP 406)
- It rejects placeholder contact details in the user agent ("leadline.example.com").
  The app now identifies itself honestly as LeadlineBot.

### Kenya's tender portal rate-limited the first live run
- The portal answers "too many requests" to quick bursts. Leadline paces its page
  requests, waits out a rate-limit reply once, and reuses the tender list for 30
  minutes across runs (about 400 open tenders, 5 requests).
