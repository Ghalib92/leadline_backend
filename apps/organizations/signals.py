"""Signals other apps subscribe to, so this app never imports them.

Both are sent from services after the change is saved; receivers should defer real
work with transaction.on_commit.
"""

from django.dispatch import Signal

# A new file was stored. Arguments: organization_id, document_id.
document_uploaded = Signal()

# Something the AI's picture of the organization depends on changed (profile,
# preferences or the document list). Argument: organization_id.
context_changed = Signal()
