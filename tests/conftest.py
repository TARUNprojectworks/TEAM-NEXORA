import pytest

from app import create_app
from app.models import User, create_weak_spot_deck, db


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "WTF_CSRF_ENABLED": False,
        "AI_ENABLED": False,
        "STORAGE_BACKEND": "local",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    """Create a student the same way sign up does. Returns the user's id."""
    def _make_user(name="Riya", email="riya@example.com", password="password123"):
        with app.app_context():
            user = User(name=name, email=email)
            user.set_password(password)
            db.session.add(user)
            create_weak_spot_deck(user)
            db.session.commit()
            return user.id
    return _make_user


@pytest.fixture
def logged_in_client(client, make_user):
    make_user()
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client
