# Leadline: challenges and how we solved them

Raw material for LinkedIn posts. Each entry: what went wrong, why, the fix, and the
lesson, with the real numbers. Newest stage first. Added to at the end of every stage.

Context for readers: Leadline is an AI app for Kenyan businesses. It finds client leads
(businesses that need what you sell), government tenders and grants, scores them,
and writes the pitch or the bid. Built with Django, LangGraph, Groq (free tier) with
Gemini as backup, Temporal for background jobs, React Router on the front end.

---

## Stage 13 (19 Nov 2026): getting it online

### Moving the UI to Vercel made every visitor look like one person
- **What happened:** the UI renders on its own server, which calls the API for each
  visitor. On Vercel, every request reaches Django from Vercel's machines. Django's
  sign-in limit is 5 attempts a minute *per address*: on launch day it would have been
  5 a minute for all of Kenya together.
- **Why it's easy to miss:** locally there is one person, so nothing looks wrong. The
  API also has to be public now (Vercel calls it over the internet), so we can't just
  trust an "X-Forwarded-For" header: anyone can send one.
- **Fix:** the UI server sends the visitor's address with a shared secret; Django
  believes the address only when the secret matches. Our own proxy (Caddy) reports
  the connecting address for everyone else.
- **Lesson:** every time a proxy goes in front of your app, ask "who does the app
  think the visitor is now?". Rate limits, audit logs and abuse checks all depend on it.

### Vercel caps a request at 4.5 MB; tender documents are 25 MB
- **What happened:** documents went browser → UI server → API, so the UI server could
  add the login token (the browser never holds one). On Vercel that path can't carry
  more than 4.5 MB either way.
- **Fix:** transfer tickets. The UI server asks the API for a ticket that is good for
  one file, one path, one method, for 10 minutes; the browser then sends or fetches the
  file straight from the API. Still no token in the browser, and the API still checks
  membership.
- **Proof:** a 6 MB PDF, in a real browser: ticket, CORS preflight, upload, 201.
- **Lesson:** "serverless" hosting has limits that a normal server doesn't (body size,
  time). Find them before choosing where the big files flow.

### The production settings would have crashed on the first start
- **What happened:** the production settings listed WhiteNoise (a static-files library)
  that was never installed. Twelve stages of development never loaded those settings.
- **Fix:** installed it, and added a test that loads the production settings and runs
  Django's `check --deploy` on every test run.
- **Lesson:** code paths you never run locally (production settings, error pages,
  cron jobs) need a test that runs them, or the server finds the bug for you.

### Each process kept its own count
- **What happened:** Django's default cache lives inside each process. The API runs 3
  of them, plus the background worker, so every rate limit (sign-ins, the search
  API's 60 a minute) would have counted 4 times over, separately.
- **Fix:** a 64 MB Redis shared by all processes.

### 100 ms or 4 ms
- **What happened:** Vercel runs functions in the US by default. Every page makes
  several API calls, and the API server is in Europe.
- **Measured from the server:** 103 ms to Vercel's US region, 4.5 ms to Frankfurt. The
  UI now runs in Frankfurt, next to the API.
- **Lesson:** put the code that calls the database next to the database. A page that
  makes 5 calls pays the distance 5 times.

### Small things that would have cost an evening each
- The Temporal server image runs as a non-root user and couldn't write to a fresh
  Docker volume: mount it at a folder the image's user already owns.
- The test runner's fake browser sent file uploads as plain text, so the upload tests
  passed or failed for the wrong reasons. Only a real browser proved the new upload.
- digitalappex.com's email has no SPF record: mail from it would likely land in spam.
- The first deploy reported success but skipped its last steps (the nightly backup
  job). The server-side steps were piped to the server over ssh, and a
  `docker compose exec` inside them read the rest of the script as its own input. The
  steps now run from a file on the server.

---

## Stage 12 (19 Nov 2026): from a job to an application

### A list of tools became a list of achievements
- **What happened:** Brian's CV lists, under his PesaLink job, "PostgreSQL, Redis,
  Celery, Docker, AWS". The first AI-tailored CV said he "Designed and maintained
  PostgreSQL schemas, optimized queries for high throughput", and "Collaborated with
  front-end developers using Git for source control and code reviews". None of it is
  in his CV. The gap advice told him to say "all your development has been done on
  Linux servers". His CV never mentions Linux.
- **Why:** the model writes toward the posting. The posting asked for database work,
  Linux and teamwork, so the model supplied them.
- **Fix, in layers:** a prompt that names this exact pattern ("a tool listed under a
  job is something they used, not something they designed"); then checks that send the
  draft back once; then whatever is still unsupported is shown to the person as "Check
  these lines: your CV doesn't say this".
- **Result:** on every live run the first draft was sent back (3 to 9 unsupported
  claims). After one rewrite, 4 to 8 lines are left flagged, all of them fair calls.
- **Lesson:** you can't make a free model stop embellishing. You can make sure every
  embellishment is pointed out before a person signs their name under it.

### Word checks work on CV lines, not on letters
- **Measured on the real draft:** every invented CV line had 4 or more words the CV
  never uses, making up most of the line ("designed, maintained, schemas, optimized,
  queries, throughput"). The honest lines had at most 2 ("version control",
  "Agriculture and Technology" for JKUAT). Rule: 3+ new words and half the line.
- **On the cover letter the same rule failed both ways.** "I designed and maintained
  the supporting infrastructure" passed, because the posting itself says design and
  maintain. "I would welcome the opportunity to discuss…" failed.
- **Fix:** a second model reads the letter against the CV and quotes the sentences
  it can't support. Code keeps only quotes really found in the letter.
- **Lesson (again, see stage 9):** filters don't work on prose. Use code where text
  is short and factual, and a second reader where it isn't.

### The writer can't police itself; a smaller model can
- Asked to check its own kind of letter, gpt-oss-120b (the writer) found nothing, twice.
  The smaller gpt-oss-20b caught the stretched sentence 2 times in 3, with no false
  alarms once told to skip greetings and closing lines. So it reads each letter twice.
- At "high" reasoning effort the small model returned empty answers on every call:
  it spent its whole output allowance thinking. Medium works.
- The checker first got the candidate's job preferences too, and counted their wished-for
  skills as evidence. Now it sees only the CV and their own summary.

### The AI renamed M-Pesa to M-Pay
- In three different runs the writer wrote "integrated the M‑Pay Daraja API". The CV
  says M-Pesa. Word checks skipped it because "Pay" is 3 letters.
- **Fix:** names (a capital plus a hyphen, an acronym, a capital inside a word: M-Pesa,
  AWS, PesaLink, GraphQL) must appear in the CV. This is precise even on prose, so it
  checks the letter too. Detail: models write M‑Pesa with a non-breaking hyphen (U+2011),
  so hyphens are normalised before comparing.

### "Not yet: Kubernetes, Go, Kafka" kept coming back
- Brian's CV ends with a list of what he hasn't learned yet. The tailored CV copied it
  into a job application three times, despite a prompt rule. The schema's own field
  description said "and any other sections the CV has", contradicting the rule.
- **Fix:** both texts agree now, and code removes such lines (and an emptied heading).
  A CV lists what you have; what you lack goes in the gap notes.

### A clean result that was really an empty one
- Testing Gemini as the writer: no rewrite, zero lines flagged. It looked far better.
  Then the screenshot showed an empty tailored CV and a letter on one line
  ("Informatics,I am writing…"). Nothing was flagged because there was nothing to check.
- **Fix:** the schema refuses an empty CV and a letter with no line breaks, so a bad
  answer is retried (and falls back) instead of saved.
- **Lesson:** a perfect score from a quality check is a reason to look harder, not to
  celebrate. Look at the actual output.

### Posting text was thrown away
- To verify a job advert, the AI read its page, then the text was dropped. A letter
  needs the posting. Jobs now keep it (6,000 characters, never sent to the browser).
  Jobs saved earlier have their page read again when the letter is written. Both live
  jobs (MyJobMag, Greenhouse) worked that way.

### Small things
- The Word file was named "savannah-informatics-backend-developer-at-savannah-informati":
  job boards put the employer in the title already.
- The tracker's Apply button was missing for jobs saved before postings were kept.
- A gap note said Brian's "most recent title is Junior Python Developer" (it's his
  earlier job). Gap notes aren't checked in code yet.
- CV reading suggests only skills the CV's text actually names: "Kubernetes" from a
  model's guess is dropped. Brian's read suggested 1 title and 5 skills (Django REST
  Framework, Redis, Celery, AWS, Git) his preferences lacked.
- Two test browsers at once timed out the dev server. Run e2e scripts one at a time.

---

## Stage 11 (18-19 Nov 2026): job seekers

### "Find jobs from career pages" sounds easy, until you're in Kenya
- **The plan:** read jobs straight from companies' career pages through the public APIs
  of Greenhouse, Lever, Ashby and Workable. No scraping, no keys.
- **What we found:** we probed 30 Kenyan and African tech companies (M-KOPA, Cellulant,
  Twiga, Sendy, Wasoko, Paystack, Flutterwave…). Almost none use those four systems;
  the Workable ones had zero open roles that week. The global boards that work (GitLab,
  Canonical, Turing, Moniepoint) had 679 jobs, of which 13 a Kenyan could actually take.
- **Fix:** keep the career pages for remote and pan-African employers, and add Kenyan
  job boards (BrighterMonday, Fuzu, MyJobMag) for local volume.
- **Lesson:** check where your users' market actually posts before you build the
  integration everyone in Silicon Valley uses.

### "Remote" doesn't mean "remote for you"
- **What happened:** the first location filter let through "Remote, Bangalore",
  "Remote, Israel" and "Remote, South Korea" for a Nairobi developer.
- **Fix:** a remote job counts only if its location names nothing but "remote", or names
  the candidate's own country or city, or an open region (EMEA, Africa, Global).
- **Lesson:** a third of "remote" jobs are remote within one country.

### Three bugs stacked on top of each other, each hiding the next
- **Run 1: 0 jobs.** The search never touched the job sources. Each run's sources come
  from the workspace's general search settings (web, grants, procurement by default),
  narrowed to what the goal allows. For jobs, only "web" survived.
  Fix: every goal now has core sources that are always searched.
- **Run 2: 1 job.** Career pages were read, but matching demanded every search word,
  and the job-board results were list pages ("Backend developer jobs in Kenya") that
  the AI researcher is told to skip. Fix: looser matching, and list pages are read for
  their individual adverts (the same reader an organization's own sources use).
- **Run 3: 0 jobs from 15 adverts.** The adverts were found, read, and rejected one by
  one: "This is a job posting, not a grant, tender or call for proposals." The fact
  checker's rules were written for grants and tenders. Fix: it accepts a single job
  advert when jobs are what's wanted.
- **Run 4: 3 good jobs.** Backend Developer at Savannah Informatics (89%, one gap:
  GraphQL), Software Engineer at Turing (68%, "4 years against 5+ required"), Python
  Developer at Technobrain (65%). .NET and DevOps roles scored 12-38 and were dropped.
- **Lesson:** when a pipeline returns nothing, there is rarely one bug. Fix one, run it
  again, read the logs again.

### The AI guessed the candidate's gender from his name
- **What happened:** the reasons on a job said "his CV lists 4 years…" and "He holds a
  BSc". Nobody told the model the candidate's gender; it inferred it from "Brian".
- **Fix:** the job judge writes to the person ("your CV shows a BSc…") and never uses he,
  she or they, or guesses anything from a name.
- **Lesson:** an AI writing about a person makes assumptions you'd never write
  yourself. Have it speak to them instead.

### Don't spend AI calls on what code already knows
- Career-page postings come as structured records: title, location, salary, apply link.
  Asking a model to "verify" each one would have cost one call per job on a free tier
  of about 200,000 tokens a day. Leadline builds those facts in code and only uses the
  model where judgement is needed: fit against the CV, and the gaps.

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

### The backup model had quietly retired
- Adding a second Gemini key, a live check showed Google had stopped serving
  gemini-2.5-flash-lite to anyone, and gemini-2.5-flash to new accounts. The light
  tier's fallback had been failing in production without anyone noticing, because
  Groq rarely needed it. Moved to the 3.5 models, which both keys can call, and
  now check every fallback model with a real call (`ai_ping`) when keys change.
- Google answers a wrong key with HTTP 400 ("API_KEY_INVALID"), not 401 or 403, so
  the switch to the backup key didn't fire until that answer was recognised.

### Each AI grader preferred its own family
- To decide which model writes bids, the same tender was drafted by both and graded
  by both. Groq's grader gave its own draft 70 and Gemini's 64; Gemini's gave its own
  84 and Groq's 72. The overall scores said nothing. What both graders agreed on was
  honesty, and reading the drafts confirmed it: one invented "completed on time and
  within budget", the other wrote a [TODO]. Lesson: an AI judge needs a neutral
  provider and a reading by a person, and one score per criterion beats one total.

### A free AI tier that learns from what you send
- Writing the privacy page meant listing where user data goes, and that turned up a
  problem: Google's free Gemini tier uses prompts to improve its products, lets people
  read them, and its terms say not to send personal or confidential data. Leadline had
  just moved CVs and company documents to Gemini first. Now every AI task is private by
  default, only tasks that read public pages may use free Gemini, and a setting lists
  the providers trusted with private data. Lesson: write the privacy notice early; it
  is an audit of the system, not paperwork.

### Words only the builders understood
- The sidebar said "Workers", "Outreach", "Integrations" and put setup at the bottom.
  Mohaa's test: would a business owner know what each one is? Now the labels say what
  things do ("Searches", "Funder emails") in the order people use the app, and a short
  tour walks through it once.

### An email you could see but the form couldn't
- Mohaa typed an email into Invite and got "Add at least one email" with the address
  right there. The box only turned text into an entry on Enter; clicking a button left
  it as loose text the form never saw (and onboarding dropped the invite silently).
  Every list box now keeps what was typed when you leave it, and email boxes split
  pasted lists on spaces too. Lesson: a hidden "press Enter" rule is a bug, not a
  tip.

### Free map servers fail together
- The first live client search lost its Nairobi half: both public OpenStreetMap
  servers answered 502/504 at once. A third mirror answered in two seconds from the
  production server, so there are now four, tried in order. The same search then
  succeeded with 19 leads. Lesson: test fallbacks from the server, not your laptop;
  two mirrors that fail from home may differ from the ones that fail in Frankfurt.
