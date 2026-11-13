"""Demo data: two accounts, a complete organization and its whole pipeline.

    python manage.py seed            # create (skips if the demo org exists)
    python manage.py seed --reset    # delete the demo org and accounts, then create

Funders and calls are fictional; their links point at example.org.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.discovery.models import DiscoveryRun, RunCandidate, RunEvent, SearchAgent
from apps.opportunities.dedupe import title_key, url_key
from apps.opportunities.models import Opportunity, OpportunityReview
from apps.organizations import services as org_services
from apps.organizations.models import Organization, OrganizationMembership
from apps.outreach.models import Contact, ContactSearch, OutreachEmail
from apps.proposals.models import Proposal, ProposalEvent, ProposalVersion

PASSWORD = "LeadlineDemo2026!"
ORG_NAME = "Tujenge Data Initiative"
ADMIN = ("amina.otieno@example.org", "Amina", "Otieno")
MEMBER = ("brian.kamau@example.org", "Brian", "Kamau")
MODELS = {
    "supervisor": "openai/gpt-oss-120b",
    "researcher": "openai/gpt-oss-20b",
    "verifier": "openai/gpt-oss-20b",
    "judge": "openai/gpt-oss-120b",
}
PROMPTS = {
    "supervisor": "supervisor/v1",
    "researcher": "researcher/v1",
    "verifier": "verifier/v1",
    "judge": "judge/v2",
}

# (key, title, funder, kind, score, min, max, deadline_days, status, reasons, concerns)
OPPORTUNITIES = [
    (
        "youth",
        "Youth Data Skills Challenge Fund 2026",
        "Savannah Futures Foundation",
        "grant",
        91,
        50_000,
        150_000,
        34,
        "pursuing",
        [
            "Trains young people in data skills, your core programme",
            "Open to registered NGOs in Kenya and Uganda",
            "Grant size fits your annual budget",
        ],
        ["Requires a co-funding commitment of 10%"],
    ),
    (
        "county",
        "County Health Data Dashboards: Request for Proposals",
        "Lake Region Development Fund",
        "rfp",
        84,
        80_000,
        120_000,
        21,
        "pursuing",
        [
            "Data dashboards for county governments match your track record",
            "Kisumu and Siaya are in your operating area",
        ],
        ["Bidders must show two similar public-sector contracts"],
    ),
    (
        "girls",
        "Girls in STEM Scholarship Partnership",
        "Pan-African Digital Skills Trust",
        "partnership",
        78,
        None,
        None,
        45,
        "new",
        ["Partnership to run bootcamps for girls aged 15-19", "Values women-led delivery teams"],
        [],
    ),
    (
        "open-data",
        "Open Data for Agriculture Innovation Grants",
        "Rift Valley Agri Innovation Hub",
        "grant",
        73,
        20_000,
        60_000,
        28,
        "new",
        ["Open data and training for smallholder cooperatives", "Funds Kenyan non-profits"],
        ["Proof of a farmer-cooperative partner is required"],
    ),
    (
        "teachers",
        "Digital Teacher Training Tender (Mlima County)",
        "Mlima County Department of Education",
        "tender",
        69,
        35_000,
        35_000,
        12,
        "new",
        ["Teacher training in data literacy", "County-level tender in Kenya"],
        ["Tender security of 2% must be lodged with the bid"],
    ),
    (
        "fellowship",
        "Civic Tech Fellowship 2027",
        "Nile Basin Civic Tech Network",
        "fellowship",
        64,
        12_000,
        12_000,
        52,
        "new",
        ["Funds a staff member to build civic data tools"],
        [],
    ),
    (
        "summit",
        "Africa Data Skills Summit: Call for Speakers",
        "Data for Development Alliance",
        "conference",
        58,
        None,
        None,
        9,
        "new",
        ["A chance to present your youth data programme"],
        [],
    ),
    (
        "climate",
        "Climate Data Literacy Micro-grants",
        "Green Horizons Fund",
        "grant",
        66,
        5_000,
        15_000,
        40,
        "reviewing",
        ["Data literacy for climate resilience in East Africa"],
        [],
    ),
    (
        "health",
        "Community Health Worker Analytics Pilot",
        "Coastal Communities Fund",
        "grant",
        71,
        40_000,
        90_000,
        3,
        "submitted",
        ["Analytics training for community health workers", "You have run two similar pilots"],
        [],
    ),
    (
        "west",
        "West Africa Youth Employment Grant",
        "Gulf of Guinea Opportunity Fund",
        "grant",
        57,
        100_000,
        300_000,
        60,
        "dismissed",
        ["Youth employment through digital skills"],
        ["Only open to Ghana, Togo and Benin"],
    ),
    (
        "small",
        "Community Library Book Drive Sponsorship",
        "Readers First Society",
        "sponsorship",
        55,
        500,
        2_000,
        30,
        "dismissed",
        ["Education focus"],
        [],
    ),
    (
        "accelerator",
        "EdTech Scale-up Accelerator Cohort 5",
        "Highlands Impact Ventures",
        "accelerator",
        61,
        None,
        None,
        26,
        "new",
        ["Supports education organizations to scale programmes"],
        ["Prefers revenue-generating social enterprises"],
    ),
]

SECTIONS = [
    ("summary", "Executive summary", 250),
    ("problem", "Problem and need", 400),
    ("approach", "Our approach", 600),
    ("impact", "Expected results and impact", 400),
    ("team", "Team and track record", 300),
    ("budget", "Budget summary", 250),
]


class Command(BaseCommand):
    help = "Create demo accounts and a complete demo organization (fictional funders)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the demo data first.")

    def handle(self, *args, reset=False, **options):
        users = get_user_model().objects
        if reset:
            Organization.objects.filter(name=ORG_NAME).delete()
            users.filter(email__in=[ADMIN[0], MEMBER[0]]).delete()
        if Organization.objects.filter(name=ORG_NAME).exists():
            self.stdout.write(self.style.WARNING("Demo data exists; use --reset to recreate."))
            return
        with transaction.atomic():
            admin = self._user(*ADMIN)
            member = self._user(*MEMBER)
            org = self._organization(admin, member)
            agents = self._agents(org, admin, member)
            opportunities = self._runs_and_opportunities(org, agents, admin)
            self._reviews(org, opportunities, admin, member)
            self._proposal(org, opportunities["youth"], admin)
            self._outreach(org, opportunities, admin, member)
        self.stdout.write(self.style.SUCCESS(f'Seeded "{ORG_NAME}".'))
        for email, first, _ in (ADMIN, MEMBER):
            role = "admin" if email == ADMIN[0] else "member"
            self.stdout.write(f"  {first} ({role}): {email} / {PASSWORD}")

    # ------------------------------------------------------------------

    def _user(self, email, first, last):
        user = get_user_model().objects.create_user(
            email=email, password=PASSWORD, is_verified=True
        )
        user.profile.first_name, user.profile.last_name = first, last
        user.profile.save()
        return user

    def _organization(self, admin, member):
        org = org_services.create_organization(
            actor=admin,
            name=ORG_NAME,
            country="KE",
            organization_type="ngo",
        )
        org_services.update_organization(
            organization_id=org.pk,
            actor=admin,
            legal_name="Tujenge Data Initiative Trust",
            year_founded=2017,
            team_size="11-50",
            annual_budget="250k_1m",
            website="https://example.org/tujenge",
            contact_email="hello@example.org",
            city="Kisumu",
            description=(
                "Tujenge Data Initiative is a Kenyan non-profit that trains young people, "
                "teachers and county officials to collect, analyse and use data."
            ),
            mission="Close the data skills gap in East Africa so communities can make "
            "decisions with evidence.",
            offerings=(
                "12-week youth data bootcamps; data literacy training for teachers; "
                "dashboards and analytics support for county health departments."
            ),
            target_beneficiaries="Young people aged 18-30, teachers, county governments",
            track_record=(
                "2,400 young people trained since 2018, 68% employed within six months; "
                "health dashboards for Kisumu and Siaya counties; 300 teachers trained."
            ),
            past_funding="Lake Region Development Fund (2023), Green Horizons Fund (2024)",
            sectors=["education", "technology", "health"],
            sdgs=[4, 8, 17],
            operating_countries=["KE", "UG", "TZ"],
            languages=["en", "sw"],
        )
        org_services.update_preferences(
            organization_id=org.pk,
            actor=admin,
            opportunity_types=["grant", "rfp", "tender", "partnership", "fellowship"],
            geographic_scopes=["national", "regional"],
            target_countries=["KE", "UG", "TZ"],
            target_regions=["east_africa"],
            focus_areas=["data skills", "youth employment", "health data"],
            keywords=["data literacy", "youth", "digital skills", "open data"],
            eligibility=["registered_nonprofit", "women_led"],
            min_funding_amount=Decimal("10000"),
            max_funding_amount=Decimal("250000"),
            funding_currency="USD",
        )
        org_services.add_link(
            organization_id=org.pk, actor=admin, url="https://example.org/tujenge"
        )
        OrganizationMembership.objects.create(organization=org, user=member, role="member")
        return org

    def _agents(self, org, admin, member):
        return [
            SearchAgent.objects.create(
                organization=org,
                owner=admin,
                name="East Africa education grants",
                description="Grants and RFPs for youth data skills and teacher training.",
                opportunity_types=["grant", "rfp", "fellowship", "scholarship"],
                keywords=["data skills", "youth", "teacher training"],
                sources=["web", "grants"],
                schedule="weekly",
            ),
            SearchAgent.objects.create(
                organization=org,
                owner=member,
                name="County tenders and partnerships",
                description="County tenders, partnerships and events in Kenya.",
                opportunity_types=["tender", "partnership", "conference", "accelerator"],
                keywords=["county", "health data", "dashboard"],
                sources=["web", "procurement", "news"],
                schedule="manual",
            ),
        ]

    def _runs_and_opportunities(self, org, agents, admin):
        now = timezone.now()
        by_key = {}
        plan = [  # (agent index, days ago, status, opportunity keys, error)
            (0, 40, "succeeded", ["west", "small"], ""),
            (1, 33, "succeeded", ["health"], ""),
            (0, 26, "partial", ["climate"], "Page limit reached (40 pages)."),
            (1, 19, "failed", [], "The search stopped because of an unexpected error."),
            (0, 12, "succeeded", ["youth", "open-data", "fellowship", "girls"], ""),
            (1, 2, "succeeded", ["county", "teachers", "summit", "accelerator"], ""),
        ]
        rows = {row[0]: row for row in OPPORTUNITIES}
        for index, days_ago, status, keys, error in plan:
            agent = agents[index]
            started = now - timedelta(days=days_ago, hours=3)
            seconds = 48 if status != "failed" else 11
            run = DiscoveryRun.objects.create(
                organization=org,
                agent=agent,
                agent_name=agent.name,
                trigger="schedule" if index == 0 else "manual",
                triggered_by=None if index == 0 else admin,
                status=status,
                started_at=started,
                finished_at=started + timedelta(seconds=seconds),
                config={"match_threshold": 55},
                budget={
                    "max_searches": 12,
                    "max_pages": 40,
                    "max_tokens": 200000,
                    "max_wall_seconds": 600,
                },
                usage={
                    "searches": 6,
                    "pages": 14 if status != "failed" else 0,
                    "tokens": 61234 if status != "failed" else 3100,
                    "seconds": seconds,
                },
                sources_scanned=4 if status != "failed" else 1,
                pages_read=14 if status != "failed" else 0,
                found=len(keys),
                runner="graph",
                prompt_versions=PROMPTS,
                models_used=MODELS,
                error=error,
            )
            DiscoveryRun.objects.filter(pk=run.pk).update(created_at=started)
            found = []
            for key in keys:
                opp = self._opportunity(org, agent, run, rows[key], started)
                by_key[key] = opp
                found.append(opp)
            self._events(run, agent, found, started, status)
            self._candidates(run, found)
        return by_key

    def _opportunity(self, org, agent, run, row, found_at):
        key, title, funder, kind, score, low, high, due, status, reasons, concerns = row
        url = f"https://example.org/{key}-{funder.lower().split()[0]}"
        deadline = (timezone.localtime() + timedelta(days=due)).replace(
            hour=23, minute=59, second=0, microsecond=0
        )
        return Opportunity.objects.create(
            organization=org,
            agent=agent,
            run=run,
            opportunity_type=kind,
            title=title,
            funder=funder,
            summary=(
                f'{funder} invites proposals for "{title}". Funding supports organizations '
                "in East Africa that build practical data skills, with a focus on young "
                "people and public-sector partners."
            ),
            eligibility="Registered non-profits or companies operating in East Africa.",
            amount_min=low,
            amount_max=high,
            currency="USD" if low else "",
            deadline=deadline,
            location="Kenya" if kind in ("tender", "rfp") else "East Africa",
            source_url=url,
            source_name="example.org",
            match_score=score,
            match_reasons=reasons,
            status=status,
            saved=key in ("girls", "climate"),
            requirements={
                "items": ["Budget", "Workplan", "Registration certificate"],
                "concerns": concerns,
            },
            found_at=found_at,
            dedupe_key=url_key(url),
            title_key=title_key(title, funder),
        )

    def _events(self, run, agent, found, started, status):
        seq = 0

        def emit(step, message, *, level="info", tool="", source="", url="", data=None):
            nonlocal seq
            seq += 1
            RunEvent.objects.create(
                run=run,
                seq=seq,
                step=step,
                level=level,
                message=message,
                tool=tool,
                source=source,
                url=url,
                data=data or {},
                at=started + timedelta(seconds=seq * 2),
            )

        emit("profile", f"Reading the profile of {ORG_NAME}")
        emit("profile", "Using the company dossier", tool="dossier")
        emit(
            "profile",
            "Planned 3 search(es)",
            tool="supervisor",
            data={"searches": [{"source": "web", "query": "youth data skills grant Kenya"}]},
        )
        emit(
            "search",
            'Searching the web for "youth data skills grant Kenya"',
            tool="search",
            source="the web",
        )
        if status == "failed":
            emit("search", "Search provider did not respond", level="error", source="the web")
            DiscoveryRun.objects.filter(pk=run.pk).update(last_event_seq=seq)
            return
        emit("search", "9 result(s) from the web")
        emit(
            "search",
            'Searching grants databases for "data literacy"',
            tool="search",
            source="grants databases",
        )
        emit("search", "Picked 5 of 14 result(s) to read", tool="researcher")
        for opp in found:
            emit(
                "read",
                f"Reading example.org: {opp.title[:80]}",
                tool="fetch",
                source="example.org",
                url=opp.source_url,
            )
        emit("read", 'Skipped "Regional Data Prize 2025": it is closed')
        emit(
            "read",
            "Could not read facebook.com: The site's robots.txt does not allow automated reading.",
            level="warning",
            source="facebook.com",
        )
        emit("match", f"Scoring {len(found) + 1} opportunity(ies)", tool="judge")
        for opp in found:
            emit(
                "match",
                f"{opp.title[:80]}: {opp.match_score}/100",
                tool="judge",
                data={"score": opp.match_score},
            )
        if status == "partial":
            emit("save", "Page limit reached (40 pages).", level="warning")
        emit(
            "save",
            f"Added {len(found)} new; 0 already in your feed; 1 below the 55 threshold",
            data={"added": len(found), "duplicate": 0, "below_threshold": 1},
        )
        DiscoveryRun.objects.filter(pk=run.pk).update(last_event_seq=seq)

    def _candidates(self, run, found):
        for opp in found:
            RunCandidate.objects.create(
                run=run,
                title=opp.title,
                funder=opp.funder,
                source_name=opp.source_name,
                source_url=opp.source_url,
                opportunity_type=opp.opportunity_type,
                match_score=opp.match_score,
                match_reasons=opp.match_reasons,
                decision="added",
                opportunity=opp,
            )
        if found:
            RunCandidate.objects.create(
                run=run,
                title="Coding Club Equipment Grant",
                funder="Westlands Rotary",
                source_name="example.org",
                source_url="https://example.org/coding-club",
                opportunity_type="grant",
                match_score=41,
                match_reasons=["Education focus, but hardware only"],
                decision="below_threshold",
            )

    def _reviews(self, org, opps, admin, member):
        def review(key, reviewer, decision, *, reason="", comment="", before="new", days=1):
            opp = opps[key]
            row = OpportunityReview.objects.create(
                organization=org,
                opportunity=opp,
                reviewer=reviewer,
                decision=decision,
                reason=reason,
                comment=comment,
                status_before=before,
                match_score=opp.match_score,
                snapshot={"title": opp.title, "funder": opp.funder},
                judge_prompt="judge/v2",
                judge_model=MODELS["judge"],
            )
            OpportunityReview.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(days=days)
            )

        review("youth", admin, "approve", comment="Perfect fit for the bootcamp.", days=10)
        review("county", member, "approve", comment="We have the Kisumu dashboards.", days=1)
        review("health", admin, "approve", days=30)
        review(
            "west",
            admin,
            "reject",
            reason="wrong_location",
            comment="Only open to Ghana, Togo and Benin.",
            days=38,
        )
        review(
            "small",
            member,
            "reject",
            reason="amount_too_small",
            comment="Too small to be worth a proposal.",
            days=37,
        )

    def _proposal(self, org, opp, admin):
        now = timezone.now()
        drafted = now - timedelta(days=9)
        sections = [self._section(key, title, words) for key, title, words in SECTIONS]
        proposal = Proposal.objects.create(
            organization=org,
            opportunity=opp,
            created_by=admin,
            status="ready",
            job="draft",
            job_requested_by=admin,
            job_queued_at=drafted,
            job_started_at=drafted,
            version=2,
            runner="graph",
            prompt_versions={"drafter": "drafter/v1", "proposal_review": "proposal_review/v1"},
            models_used={"drafter": MODELS["judge"]},
            usage={"tokens": 18450, "seconds": 112},
            drafted_at=drafted,
            requirements={
                "summary": "Grants of USD 50,000-150,000 for 18-month programmes that "
                "train young people in data skills and link them to jobs.",
                "sections": [{"title": t, "guidance": "", "word_limit": w} for _, t, w in SECTIONS],
                "evaluation_criteria": [
                    "Relevance to the fund's goals (30%)",
                    "Feasibility and team (25%)",
                    "Employment outcomes (30%)",
                    "Value for money (15%)",
                ],
                "eligibility": [
                    "Registered non-profit in Kenya, Uganda or Tanzania",
                    "At least 3 years of youth programmes",
                ],
                "documents_required": [
                    "Budget (template)",
                    "Registration certificate",
                    "Audited accounts 2025",
                ],
                "budget_rules": ["10% co-funding", "Overheads at most 12%"],
                "format_rules": ["PDF, at most 10 pages"],
                "deadline": opp.deadline.date().isoformat(),
            },
            review={
                "score": 74,
                "strengths": [
                    "Clear employment outcomes backed by past results",
                    "Strong county partnerships",
                ],
                "issues": [
                    {
                        "section": "budget",
                        "issue": "Co-funding source is not named.",
                        "suggestion": "Name the 10% co-funding source and amount.",
                    },
                    {
                        "section": "impact",
                        "issue": "Targets lack a baseline.",
                        "suggestion": "Add the 2025 baseline for youth employment.",
                    },
                ],
                "missing_requirements": ["Audited accounts 2025"],
            },
        )
        ProposalVersion.objects.create(
            proposal=proposal,
            number=1,
            sections=sections,
            source="ai_draft",
            note="First draft",
            created_by=None,
        )
        edited = [dict(s) for s in sections]
        edited[0]["content"] = edited[0]["content"].replace(
            "[TODO: confirm the cohort size]", "four cohorts of 150"
        )
        ProposalVersion.objects.create(
            proposal=proposal,
            number=2,
            sections=edited,
            source="edit",
            note="Executive summary",
            created_by=admin,
        )
        for seq, (step, message) in enumerate(
            [
                ("requirements", "Reading the call: Youth Data Skills Challenge Fund 2026"),
                ("requirements", "Found 6 sections to write and 4 evaluation criteria"),
                ("outline", "Planned 6 sections"),
                ("evidence", 'Searching company documents for "track record"'),
                ("draft", "Writing 6 sections"),
                ("review", "Readiness 74/100; 2 issues to fix"),
            ],
            start=1,
        ):
            ProposalEvent.objects.create(
                proposal=proposal,
                seq=seq,
                step=step,
                message=message,
                at=drafted + timedelta(seconds=seq * 15),
            )
        Proposal.objects.filter(pk=proposal.pk).update(last_event_seq=6)

    def _section(self, key, title, words):
        evidence = [
            {
                "n": 1,
                "document_id": "",
                "document_title": "Annual report 2025",
                "location": "page 4",
                "excerpt": "2,400 young people trained since 2018; 68% in work within six months.",
            },
            {
                "n": 2,
                "document_id": "",
                "document_title": "Kisumu County partnership MoU",
                "location": "page 1",
                "excerpt": "The County Department of Health will host interns from the programme.",
            },
        ]
        content = {
            "summary": (
                "Tujenge Data Initiative will train [TODO: confirm the cohort size] young "
                "people in Kisumu, Siaya and Kampala over 18 months in practical data skills, "
                "and place them in county health departments and local employers.\n\n"
                "Since 2018 we have trained 2,400 young people, and 68% were in work within "
                "six months [1]. This programme builds on that model with paid placements "
                "agreed with county governments [2]."
            ),
            "problem": (
                "Counties collect more data than ever, but few staff can analyse it. Youth "
                "unemployment in the Lake Region is above 35%, while employers report unfilled "
                "analyst roles.\n\n- Health records are digitised but rarely used for planning\n"
                "- Graduates lack practical, job-ready data skills [1]"
            ),
            "approach": (
                "**12-week bootcamps** in spreadsheets, SQL, Python and dashboards, followed by "
                "**a 3-month paid placement** with a county partner [2].\n\n"
                "1. Recruit with youth groups, at least 50% young women\n"
                "2. Train in cohorts of 40 with mentors from partner employers\n"
                "3. Place graduates and track employment for 12 months"
            ),
            "impact": (
                "By the end of the grant: 600 young people trained, 70% in work within six "
                "months, and 3 county dashboards used in budget planning. [TODO: add the 2025 "
                "baseline for youth employment]"
            ),
            "team": (
                "Our 24-person team has run youth data programmes since 2017 and built the "
                "health dashboards used by Kisumu and Siaya counties [1][2]."
            ),
            "budget": (
                "Total USD 142,000 over 18 months: training 58%, placements stipends 22%, staff "
                "12%, monitoring 8%. Co-funding of 10% from [TODO: name the co-funding source]."
            ),
        }[key]
        missing = [part.split(":", 1)[1].strip(" ]") for part in content.split("[TODO")[1:]]
        return {
            "key": key,
            "title": title,
            "purpose": f'What the funder needs from "{title}".',
            "key_points": [],
            "evidence_query": title.lower(),
            "target_words": words,
            "content": content,
            "evidence": evidence,
            "missing_information": [m.split("]")[0] for m in missing],
            "drafted": True,
        }

    def _outreach(self, org, opps, admin, member):
        youth, county = opps["youth"], opps["county"]
        ContactSearch.objects.create(
            organization=org,
            opportunity=youth,
            status="done",
            requested_by=admin,
            started_at=timezone.now() - timedelta(days=8),
            finished_at=timezone.now() - timedelta(days=8),
            found=3,
            prompt_versions={"lead_scout": "lead_scout/v1"},
            models_used={"lead_scout": MODELS["researcher"]},
        )
        grace = Contact.objects.create(
            organization=org,
            opportunity=youth,
            name="Grace Wanjiru",
            role="Programme Officer, Youth Skills",
            email="grace.wanjiru@example.org",
            kind="person",
            relevance="Named as the contact for questions about the call.",
            confidence=92,
            source="page",
            source_url=youth.source_url,
        )
        Contact.objects.create(
            organization=org,
            opportunity=youth,
            name="",
            role="Grants team",
            email="grants@example.org",
            kind="inbox",
            relevance="The fund's general grants inbox.",
            confidence=70,
            source="page",
            source_url="https://example.org/contact",
        )
        Contact.objects.create(
            organization=org,
            opportunity=youth,
            name="Daniel Mwangi",
            role="Partnerships Director",
            phone="+254 700 000 123",
            kind="person",
            relevance="Leads the foundation's partnerships in Kenya.",
            confidence=60,
            source="page",
            source_url="https://example.org/team",
        )
        peter = Contact.objects.create(
            organization=org,
            opportunity=county,
            name="Peter Ochieng",
            role="Procurement Officer",
            email="procurement@example.org",
            kind="person",
            relevance="Named in the RFP for clarification questions.",
            confidence=88,
            source="page",
            source_url=county.source_url,
            created_by=None,
        )
        sent_at = timezone.now() - timedelta(days=7)
        OutreachEmail.objects.create(
            organization=org,
            opportunity=youth,
            contact=grace,
            to_email=grace.email,
            to_name=grace.name,
            purpose="question",
            subject="Question about co-funding for the Youth Data Skills Challenge Fund",
            body=(
                "Dear Grace,\n\nWe are preparing a proposal for the Youth Data Skills Challenge "
                "Fund 2026. Could in-kind contributions from county partners count towards the "
                "10% co-funding?\n\nThank you,\nAmina Otieno\nTujenge Data Initiative"
            ),
            status="sent",
            created_by=admin,
            approved_by=admin,
            approved_at=sent_at,
            sent_at=sent_at,
            reply_to=admin.email,
            prompt_id="emailer/v1",
            model=MODELS["judge"],
        )
        OutreachEmail.objects.create(
            organization=org,
            opportunity=county,
            contact=peter,
            to_email=peter.email,
            to_name=peter.name,
            purpose="question",
            subject="Clarification on the County Health Data Dashboards RFP",
            body=(
                "Dear Peter,\n\nTujenge Data Initiative intends to bid for the County Health "
                "Data Dashboards RFP. Could you confirm whether dashboards delivered to Kisumu "
                "and Siaya counties count as the two similar public-sector contracts?\n\n"
                "Kind regards,\nBrian Kamau\nTujenge Data Initiative"
            ),
            status="draft",
            created_by=member,
            prompt_id="emailer/v1",
            model=MODELS["judge"],
        )
