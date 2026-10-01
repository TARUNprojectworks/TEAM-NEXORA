from app import create_app
from app.models import Deck, User, UserDeck, db


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_landing_page_shows_sign_up_and_log_in(client):
    page = client.get("/").get_data(as_text=True)
    assert "Sign up" in page
    assert "Log in" in page


def test_home_needs_login(client):
    response = client.get("/home")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_signup_creates_user_and_weak_spot_deck(app, client):
    response = client.post("/signup", data={
        "name": "Arjun", "email": "Arjun@Example.com",
        "password": "password123", "confirm": "password123",
    })
    assert response.status_code == 302

    with app.app_context():
        user = db.session.query(User).filter_by(email="arjun@example.com").one()
        deck = db.session.query(Deck).filter_by(owner_id=user.id, is_weak_spot=True).one()
        assert deck.title == "Weak spot practice"
        assert deck.folder == "personal"
        user_deck = db.session.get(UserDeck, (user.id, deck.id))
        assert user_deck.mode == "normal"
        assert user.password_hash != "password123"


def test_signup_rejects_duplicate_email(client, make_user):
    make_user()
    response = client.post("/signup", data={
        "name": "Riya", "email": "riya@example.com",
        "password": "password123", "confirm": "password123",
    })
    assert response.status_code == 200
    assert "already has an account" in response.get_data(as_text=True)


def test_signup_rejects_short_password(client):
    response = client.post("/signup", data={
        "name": "A", "email": "a@example.com", "password": "short", "confirm": "short",
    })
    assert "at least 8 characters" in response.get_data(as_text=True)


def test_login_with_wrong_password_fails(client, make_user):
    make_user()
    response = client.post("/login", data={"email": "riya@example.com", "password": "wrong-pass"})
    assert response.status_code == 200
    assert "Wrong email or password" in response.get_data(as_text=True)


def test_login_then_home_then_logout(logged_in_client):
    home = logged_in_client.get("/home")
    assert home.status_code == 200
    assert "Hi Riya" in home.get_data(as_text=True)

    logged_in_client.post("/logout")
    assert logged_in_client.get("/home").status_code == 302


def test_login_ignores_next_url_to_other_sites(client, make_user):
    make_user()
    response = client.post(
        "/login?next=https://evil.example.com/",
        data={"email": "riya@example.com", "password": "password123"},
    )
    assert response.headers["Location"] == "/home"


def test_user_text_is_escaped(app, client):
    client.post("/signup", data={
        "name": "<script>alert(1)</script>", "email": "x@example.com",
        "password": "password123", "confirm": "password123",
    })
    page = client.get("/home").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_deleting_user_removes_their_decks(app, make_user):
    user_id = make_user()
    with app.app_context():
        db.session.delete(db.session.get(User, user_id))
        db.session.commit()
        assert db.session.query(Deck).count() == 0
        assert db.session.query(UserDeck).count() == 0


def test_csrf_is_on_outside_tests(tmp_path):
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'csrf.db'}"})
    response = app.test_client().post("/login", data={"email": "a@b.co", "password": "x"})
    assert response.status_code == 400


def test_unknown_page_shows_friendly_404(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "find that page" in response.get_data(as_text=True)
