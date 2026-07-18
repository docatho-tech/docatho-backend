import pytest
from rest_framework.test import APIClient

from docatho_backend.users.models import User
from docatho_backend.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client(db):
    """Factory returning an APIClient authenticated as the given user."""

    def _make(user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _make
