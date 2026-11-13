"""Signals other apps subscribe to, so this app never imports them."""

from django.dispatch import Signal

# A person approved an opportunity: the Proposal Writer starts drafting.
# Sent after the review is committed. Arguments: organization_id, opportunity_id,
# review_id.
opportunity_approved = Signal()

# Any review (approve or reject) was committed: the learning loop updates its memory
# and preference suggestions. Arguments: organization_id, opportunity_id, review_id,
# decision.
opportunity_reviewed = Signal()
