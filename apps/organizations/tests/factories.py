from ..models import KnowledgeFolder


def make_folder(organization, name="General", **overrides):
    return KnowledgeFolder.objects.create(organization=organization, name=name, **overrides)
