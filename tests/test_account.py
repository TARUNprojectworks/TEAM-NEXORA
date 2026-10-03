"""The side menu: account, card theme, feedback, log out. Also back links, flash messages and the logo page."""

import pytest

from app.models import Deck, Feedback, User, UserDeck, db


@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def page(client, url):
    return client.get(url).get_data(as_text=True)


# ---------- Header and menu ----------

def test_header_has_menu_button_and_no_log_out(riya_client):
    html = page(riya_client, "/home")
    header = html.split('<header class="site-header">')[1].split("</header>")[0]
    assert 'id="menu-button"' in header and 'aria-label="Open menu"' in header
    assert header.index('id="menu-button"') < header.index('class="brand"')
    assert "Nexora Notebook" in header and "Log out" not in header
    assert "logos/logo-1.svg" in html


def test_menu_shows_account_theme_feedback_and_log_out(riya_client):
    drawer = page(riya_client, "/home").split('<dialog class="drawer"')[1].split("</dialog>")[0]
    for text in ["Riya", "riya@example.com", "XP", "Streak", "Member since", "Change name", "Change password",
                 "Card theme", "Feedback", "Log out"]:
        assert text in drawer


def test_logged_out_pages_have_no_menu(client):
    html = page(client, "/login")
    assert 'id="menu-button"' not in html and 'id="drawer"' not in html


def test_change_name(app, riya_client, riya):
    response = riya_client.post("/account/name", data={"name": "Riya S", "next": "/decks/mine"})
    assert response.headers["Location"].endswith("/decks/mine")
    with app.app_context():
        assert db.session.get(User, riya).name == "Riya S"


def test_change_name_rejects_an_empty_name(app, riya_client, riya):
    riya_client.post("/account/name", data={"name": "  "})
    with app.app_context():
        assert db.session.get(User, riya).name == "Riya"


def test_change_password_needs_the_current_one(app, riya_client, riya):
    riya_client.post("/account/password", data={
        "current_password": "wrong", "new_password": "newpassword1", "confirm_password": "newpassword1"})
    with app.app_context():
        assert db.session.get(User, riya).check_password("password123")

    riya_client.post("/account/password", data={
        "current_password": "password123", "new_password": "newpassword1", "confirm_password": "newpassword1"})
    with app.app_context():
        assert db.session.get(User, riya).check_password("newpassword1")


def test_change_password_rejects_short_or_mismatched(app, riya_client, riya):
    for new, confirm in [("short", "short"), ("newpassword1", "newpassword2")]:
        riya_client.post("/account/password", data={
            "current_password": "password123", "new_password": new, "confirm_password": confirm})
    with app.app_context():
        assert db.session.get(User, riya).check_password("password123")


def test_next_url_must_stay_on_this_site(riya_client):
    response = riya_client.post("/account/name", data={"name": "Riya", "next": "https://evil.example.com/"})
    assert response.headers["Location"].endswith("/home")


# ---------- Card theme ----------

def test_theme_defaults_to_index_card(app, riya):
    with app.app_context():
        assert db.session.get(User, riya).card_theme == "index"


def test_theme_saves_from_the_live_picker(app, riya_client, riya):
    response = riya_client.post("/account/theme", json={"card_theme": "night"})
    assert response.get_json() == {"card_theme": "night"}
    with app.app_context():
        assert db.session.get(User, riya).card_theme == "night"


def test_unknown_theme_is_refused(app, riya_client, riya):
    assert riya_client.post("/account/theme", json={"card_theme": "neon"}).status_code == 400
    with app.app_context():
        assert db.session.get(User, riya).card_theme == "index"


def test_study_card_and_card_maker_use_the_theme(app, riya_client, riya):
    with app.app_context():
        deck = Deck(title="Bio", folder="personal", owner_id=riya)
        db.session.add(deck)
        db.session.flush()
        from app.models import Card
        db.session.add(Card(deck_id=deck.id, question="Q", answer="A", topic="Cells", owner_id=riya))
        db.session.add(UserDeck(user_id=riya, deck_id=deck.id, mode="normal"))
        db.session.commit()
        deck_id = deck.id
    riya_client.post("/account/theme", json={"card_theme": "clean"})
    riya_client.get(f"/study/{deck_id}")
    assert page(riya_client, "/study/card").count("card-face-front theme-clean") == 1
    maker = page(riya_client, "/maker/")
    assert "Card theme for studying" in maker and "themed-card theme-clean" in maker


# ---------- Feedback ----------

def test_feedback_is_saved(app, riya_client, riya):
    riya_client.post("/account/feedback", data={"feedback": "  Please add dark mode for the whole app.  "})
    with app.app_context():
        saved = db.session.query(Feedback).one()
        assert (saved.user_id, saved.text) == (riya, "Please add dark mode for the whole app.")


def test_empty_feedback_is_not_saved(app, riya_client):
    riya_client.post("/account/feedback", data={"feedback": "   "})
    with app.app_context():
        assert db.session.query(Feedback).count() == 0


# ---------- Back links, flash messages, logo page ----------

@pytest.mark.parametrize("url, parent", [
    ("/decks/mine", 'href="/home"'),
    ("/decks/ready", 'href="/home"'),
    ("/tracking/", 'href="/home"'),
    ("/decks/new", 'href="/home"'),
    ("/logo-preview", 'href="/home"'),
])
def test_every_page_but_home_has_a_back_link(riya_client, url, parent):
    back_row = page(riya_client, url).split('<p class="back-row">')[1].split("</p>")[0]
    assert parent in back_row and "← Back" in back_row


def test_home_has_no_back_link(riya_client):
    assert 'class="back-row"' not in page(riya_client, "/home")


def test_deck_page_goes_back_to_my_decks(app, riya_client, riya):
    with app.app_context():
        deck_id = db.session.query(Deck).filter_by(owner_id=riya).first().id
    back_row = page(riya_client, f"/decks/{deck_id}").split('<p class="back-row">')[1].split("</p>")[0]
    assert 'href="/decks/mine"' in back_row


def test_flash_messages_have_a_close_button(riya_client):
    riya_client.post("/account/name", data={"name": "Riya"})
    html = page(riya_client, "/home")
    assert 'class="flash-close" aria-label="Close message"' in html


def test_logo_preview_shows_three_options(riya_client):
    html = page(riya_client, "/logo-preview")
    for number in (1, 2, 3):
        assert f"logos/logo-{number}.svg" in html
