"""Controlled vocabularies for the organization profile.

Structured values (rather than free text) let opportunity-matching agents
filter reliably, so extend these lists instead of accepting arbitrary strings.
"""

from django.db import models


class OrganizationType(models.TextChoices):
    NGO = "ngo", "Non-profit / NGO"
    COMMUNITY_BASED = "community_based", "Community-based organization"
    SOCIAL_ENTERPRISE = "social_enterprise", "Social enterprise"
    STARTUP = "startup", "Startup"
    SME = "sme", "Small or medium enterprise"
    CORPORATE = "corporate", "Large company"
    COOPERATIVE = "cooperative", "Cooperative"
    ACADEMIC = "academic", "University or research institution"
    GOVERNMENT = "government", "Government agency"
    FOUNDATION = "foundation", "Foundation"
    FAITH_BASED = "faith_based", "Faith-based organization"
    INDIVIDUAL = "individual", "Individual / sole proprietor"
    OTHER = "other", "Other"


class TeamSize(models.TextChoices):
    SIZE_1_10 = "1-10", "1–10"
    SIZE_11_50 = "11-50", "11–50"
    SIZE_51_200 = "51-200", "51–200"
    SIZE_201_500 = "201-500", "201–500"
    SIZE_501_1000 = "501-1000", "501–1,000"
    SIZE_1000_PLUS = "1000+", "More than 1,000"


class BudgetRange(models.TextChoices):
    """Annual budget or revenue, in US dollars."""

    UNDER_10K = "under_10k", "Under $10k"
    FROM_10K_TO_50K = "10k_50k", "$10k–$50k"
    FROM_50K_TO_250K = "50k_250k", "$50k–$250k"
    FROM_250K_TO_1M = "250k_1m", "$250k–$1M"
    FROM_1M_TO_5M = "1m_5m", "$1M–$5M"
    OVER_5M = "over_5m", "Over $5M"


class Goal(models.TextChoices):
    """What an organization wants to find. The organization picks one or more; each
    search agent has exactly one. apps.discovery.choices.GOAL_SCOPES says which
    opportunity types and sources belong to each."""

    CLIENTS = "clients", "Clients"
    TENDERS = "tenders", "Tenders"
    GRANTS = "grants", "Grants & funding"


class OpportunityType(models.TextChoices):
    GRANT = "grant", "Grant"
    TENDER = "tender", "Tender / procurement"
    RFP = "rfp", "Request for proposals"
    FELLOWSHIP = "fellowship", "Fellowship"
    SCHOLARSHIP = "scholarship", "Scholarship"
    ACCELERATOR = "accelerator", "Accelerator / incubator"
    COMPETITION = "competition", "Competition / challenge"
    AWARD = "award", "Award / prize"
    INVESTMENT = "investment", "Equity investment"
    LOAN = "loan", "Loan / debt financing"
    PARTNERSHIP = "partnership", "Partnership"
    CONSULTANCY = "consultancy", "Consultancy"
    SPONSORSHIP = "sponsorship", "Sponsorship"
    IN_KIND = "in_kind", "In-kind support"
    CONFERENCE = "conference", "Conference / event"
    OTHER = "other", "Other"
    # A business that could buy what the organization sells (the Clients goal).
    CLIENT = "client", "Client lead"


class GeographicScope(models.TextChoices):
    LOCAL = "local", "Local (city or county)"
    NATIONAL = "national", "National"
    REGIONAL = "regional", "Regional (multi-country)"
    INTERNATIONAL = "international", "International / global"


class Region(models.TextChoices):
    EAST_AFRICA = "east_africa", "East Africa"
    WEST_AFRICA = "west_africa", "West Africa"
    CENTRAL_AFRICA = "central_africa", "Central Africa"
    SOUTHERN_AFRICA = "southern_africa", "Southern Africa"
    NORTH_AFRICA = "north_africa", "North Africa"
    SUB_SAHARAN_AFRICA = "sub_saharan_africa", "Sub-Saharan Africa"
    AFRICA = "africa", "Africa (continent-wide)"
    MIDDLE_EAST = "middle_east", "Middle East"
    EUROPE = "europe", "Europe"
    NORTH_AMERICA = "north_america", "North America"
    LATIN_AMERICA = "latin_america", "Latin America & Caribbean"
    SOUTH_ASIA = "south_asia", "South Asia"
    EAST_ASIA = "east_asia", "East & Southeast Asia"
    OCEANIA = "oceania", "Oceania"
    GLOBAL = "global", "Global"


class Eligibility(models.TextChoices):
    """Facts funders commonly filter on. Pick all that apply."""

    REGISTERED_NONPROFIT = "registered_nonprofit", "Registered non-profit"
    REGISTERED_COMPANY = "registered_company", "Registered company"
    TAX_EXEMPT = "tax_exempt", "Tax-exempt status"
    WOMEN_LED = "women_led", "Women-led"
    YOUTH_LED = "youth_led", "Youth-led"
    DISABILITY_LED = "disability_led", "Led by persons with disabilities"
    MINORITY_LED = "minority_led", "Minority- or indigenous-led"
    REFUGEE_LED = "refugee_led", "Refugee-led"
    RURAL_BASED = "rural_based", "Rural-based"
    EARLY_STAGE = "early_stage", "Early-stage (under 3 years)"
    AUDITED_ACCOUNTS = "audited_accounts", "Has audited accounts"


class LinkKind(models.TextChoices):
    WEBSITE = "website", "Website"
    LINKEDIN = "linkedin", "LinkedIn"
    X = "x", "X (Twitter)"
    FACEBOOK = "facebook", "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    YOUTUBE = "youtube", "YouTube"
    GITHUB = "github", "GitHub"
    BLOG = "blog", "Blog / news"
    PORTFOLIO = "portfolio", "Portfolio / case studies"
    OTHER = "other", "Other"


class DocumentCategory(models.TextChoices):
    COMPANY_PROFILE = "company_profile", "Company / organization profile"
    REGISTRATION = "registration", "Registration or legal certificate"
    STRATEGY = "strategy", "Strategic plan"
    ANNUAL_REPORT = "annual_report", "Annual or impact report"
    FINANCIAL = "financial", "Financial statements / budget"
    PROPOSAL = "proposal", "Past proposal or application"
    PITCH_DECK = "pitch_deck", "Pitch deck"
    CASE_STUDY = "case_study", "Case study / project report"
    POLICY = "policy", "Policy document"
    CV = "cv", "Team CVs / bios"
    OTHER = "other", "Other"


class ProcessingStatus(models.TextChoices):
    """Tracks AI ingestion of a document (text extraction, chunking and embedding)."""

    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"  # worth retrying
    # The file type, or a scanned PDF without a text layer, cannot be read yet.
    # Retrying will not help; the user can upload a PDF or DOCX instead.
    UNSUPPORTED = "unsupported", "Unsupported"


# ---------------------------------------------------------------------------
# Clients brief: who the organization sells to
# ---------------------------------------------------------------------------


class BusinessCategory(models.TextChoices):
    """Kinds of business a Clients agent looks for (mapped to map/place categories)."""

    HOSPITALITY = "hospitality", "Hotels, lodges & guest houses"
    RESTAURANTS = "restaurants", "Restaurants, cafés & bars"
    HEALTH = "health", "Clinics, hospitals & pharmacies"
    EDUCATION = "education", "Schools, colleges & training centres"
    LEGAL = "legal", "Law firms"
    PROFESSIONAL = "professional", "Accounting & consulting firms"
    REAL_ESTATE = "real_estate", "Real estate & property"
    RETAIL = "retail", "Shops & supermarkets"
    BEAUTY = "beauty", "Salons, barbers & spas"
    FITNESS = "fitness", "Gyms & fitness"
    AUTOMOTIVE = "automotive", "Garages & car dealers"
    LOGISTICS = "logistics", "Transport & logistics"
    FINANCE = "finance", "SACCOs, microfinance & insurance"
    MANUFACTURING = "manufacturing", "Manufacturing"
    AGRICULTURE = "agriculture", "Farms & agribusiness"
    CONSTRUCTION = "construction", "Construction & hardware"
    EVENTS = "events", "Event venues & planners"
    TRAVEL = "travel", "Tour operators & travel agents"
    NGO = "ngo", "NGOs & foundations"
    RELIGIOUS = "religious", "Churches & religious organizations"
    TECH = "tech", "Tech companies & startups"
    MEDIA = "media", "Media & creative agencies"
    OTHER = "other", "Other"


class BusinessSize(models.TextChoices):
    SOLO = "solo", "Sole trader"
    SMALL = "small", "Small (2–10 people)"
    MEDIUM = "medium", "Medium (11–50 people)"
    LARGE = "large", "Large (more than 50)"


class LeadSignal(models.TextChoices):
    """Signs a business needs what the organization sells; Clients agents check for these."""

    NO_WEBSITE = "no_website", "No website"
    BROKEN_WEBSITE = "broken_website", "Website is down or broken"
    OUTDATED_WEBSITE = "outdated_website", "Website looks outdated"
    NOT_MOBILE_FRIENDLY = "not_mobile_friendly", "Website not mobile-friendly"
    SLOW_WEBSITE = "slow_website", "Slow website"
    NO_HTTPS = "no_https", "No secure (HTTPS) website"
    SOCIAL_ONLY = "social_only", "Only on social media"
    NO_ONLINE_BOOKING = "no_online_booking", "No online booking or ordering"
    NO_CHATBOT = "no_chatbot", "No chat or quick replies"
    NO_ONLINE_PAYMENTS = "no_online_payments", "No online payments"
    MANY_REVIEWS = "many_reviews", "Busy: many reviews or followers"
    NEW_BUSINESS = "new_business", "Recently opened"
    HIRING = "hiring", "Hiring or growing"


# ---------------------------------------------------------------------------
# Tenders brief: what the organization bids for
# ---------------------------------------------------------------------------


class TenderCategory(models.TextChoices):
    SOFTWARE = "software", "Software, systems & apps"
    IT_HARDWARE = "it_hardware", "IT hardware & networks"
    IT_SERVICES = "it_services", "IT support & managed services"
    CONSULTANCY = "consultancy", "Consultancy & advisory"
    MARKETING = "marketing", "Marketing, media & design"
    TRAINING = "training", "Training & capacity building"
    RESEARCH = "research", "Research, surveys & evaluation"
    CONSTRUCTION = "construction", "Construction & works"
    SUPPLIES = "supplies", "Goods & supplies"
    MEDICAL = "medical", "Medical supplies & equipment"
    FACILITIES = "facilities", "Cleaning, security & facilities"
    TRANSPORT = "transport", "Transport & logistics"
    ENERGY = "energy", "Energy & utilities"
    AGRICULTURE = "agriculture", "Agriculture & food"
    OTHER = "other", "Other"


class BuyerType(models.TextChoices):
    NATIONAL_GOVERNMENT = "national_government", "National government"
    COUNTY_GOVERNMENT = "county_government", "County or local government"
    PARASTATAL = "parastatal", "State corporations & parastatals"
    INTERNATIONAL = "international", "UN, donors & international NGOs"
    NGO = "ngo", "Local NGOs"
    EDUCATION = "education", "Universities & schools"
    PRIVATE = "private", "Private companies"


class TenderEligibility(models.TextChoices):
    """Facts that open or close tenders (Kenya's AGPO reserves 30% of public tenders)."""

    AGPO_YOUTH = "agpo_youth", "AGPO: youth-owned"
    AGPO_WOMEN = "agpo_women", "AGPO: women-owned"
    AGPO_PWD = "agpo_pwd", "AGPO: owned by persons with disabilities"
    LOCAL_FIRM = "local_firm", "Locally owned firm"
    REGISTERED_SUPPLIER = "registered_supplier", "Registered government supplier"
    TAX_COMPLIANT = "tax_compliant", "Valid tax compliance certificate"
    INTERNATIONAL_BIDDER = "international_bidder", "Can bid on international tenders"
