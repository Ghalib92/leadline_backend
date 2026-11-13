from apps.common.jobs import job

from . import services


@job("learning.refresh_suggestions")
def refresh_suggestions(organization_id):
    services.refresh_suggestions(organization_id)
