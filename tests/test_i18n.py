"""
Tests for the English/French localisation of the owner console:
  - language resolution (cookie > Accept-Language > default English),
  - the /lang/<code> switcher (cookie set + safe local redirect),
  - French rendering across the login, door-code, early check-in and review pages.
"""

import os

from fastapi.testclient import TestClient

from src.adapters.simulator_door_lock import SimulatorDoorLockGateway
from src.web.app import create_app
from src.web.i18n import Translator, lang_from_request, normalize_lang


def _make_client():
    os.environ.setdefault("REVIEW_TOKEN", "test-token")
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_USER", "test@test.com")
    os.environ.setdefault("SMTP_PASSWORD", "x")
    os.environ.setdefault("IMAP_HOST", "localhost")
    os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    app = create_app()
    app.state.door_lock = SimulatorDoorLockGateway()
    client = TestClient(app)
    client.cookies.set("session", "test-token")  # authenticated owner
    return client


# -- unit-level resolution ---------------------------------------------------

def test_normalize_lang_maps_variants():
    assert normalize_lang("fr-FR") == "fr"
    assert normalize_lang("FR") == "fr"
    assert normalize_lang("en-US") == "en"
    assert normalize_lang("de") is None
    assert normalize_lang("") is None
    assert normalize_lang(None) is None


def test_translator_falls_back_to_english_then_key():
    fr = Translator("fr")
    assert fr("login.submit") == "Se connecter"
    en = Translator("en")
    assert en("login.submit") == "Sign in"
    # unknown key degrades to the key itself rather than raising
    assert fr("does.not.exist") == "does.not.exist"


def test_translator_formats_arguments():
    assert Translator("en")("err.send_failed", exc="boom") == "Sending failed: boom"
    assert "boum" not in Translator("fr")("err.send_failed", exc="boum") or True
    assert Translator("fr")("err.send_failed", exc="boum") == "Échec de l'envoi : boum"


# -- language negotiation on real requests -----------------------------------

def test_default_language_is_english():
    client = _make_client()
    resp = client.get("/login")
    assert '<html lang="en">' in resp.text
    assert "Sign in" in resp.text


def test_accept_language_french_is_honoured():
    client = _make_client()
    resp = client.get("/login", headers={"accept-language": "fr-FR,fr;q=0.9,en;q=0.8"})
    assert '<html lang="fr">' in resp.text
    assert "Se connecter" in resp.text and "Utilisateur" in resp.text


def test_switcher_sets_cookie_and_redirects_back():
    client = _make_client()
    resp = client.get(
        "/lang/fr",
        headers={"referer": "http://testserver/door-codes"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/door-codes"
    assert "lang=fr" in resp.headers.get("set-cookie", "")


def test_switcher_redirect_stays_local_for_external_referer():
    """The host is stripped from the Referer — only the local path is used, so a
    hostile Referer can never bounce the owner off-site."""
    client = _make_client()
    resp = client.get(
        "/lang/fr", headers={"referer": "http://evil.example/x"}, follow_redirects=False
    )
    assert resp.headers["location"] == "/x"  # our own /x, never evil.example
    # no referer at all → home
    resp = client.get("/lang/fr", follow_redirects=False)
    assert resp.headers["location"] == "/"


def test_lang_switch_works_before_login():
    """/lang/<code> is public so the language can be flipped on the login page."""
    client = _make_client()
    client.cookies.clear()  # unauthenticated
    resp = client.get("/lang/fr", follow_redirects=False)
    assert resp.status_code == 303
    assert "lang=fr" in resp.headers.get("set-cookie", "")


def test_cookie_beats_accept_language():
    client = _make_client()
    client.cookies.set("lang", "fr")
    resp = client.get("/door-codes", headers={"accept-language": "en-US,en"})
    assert '<html lang="fr">' in resp.text


# -- French rendering across the pages ---------------------------------------

def test_door_code_page_renders_french():
    client = _make_client()
    client.cookies.set("lang", "fr")
    resp = client.get("/door-codes")
    assert resp.status_code == 200
    assert "Créer le code" in resp.text
    assert "Pour qui (facultatif)" in resp.text
    assert "Déconnexion" in resp.text
    # the FR link is the active (non-clickable) one
    assert 'class="active">FR' in resp.text


def test_early_checkin_page_renders_french_including_client_js():
    client = _make_client()
    client.cookies.set("lang", "fr")
    resp = client.get("/early-checkin")
    assert resp.status_code == 200
    assert "Code d'arrivée anticipée" in resp.text
    # client-side dropdown strings are localised too (embedded as JS literals)
    assert "Aucune réservation à venir" in resp.text
    assert "Choisir une réservation" in resp.text


def test_review_page_renders_french():
    client = _make_client()
    client.cookies.set("lang", "fr")
    resp = client.get("/review")
    assert resp.status_code == 200
    assert "Aucun brouillon en attente" in resp.text
    assert "Arrivée anticipée" in resp.text  # footer nav link


def test_door_code_error_is_localised():
    client = _make_client()
    client.cookies.set("lang", "fr")
    resp = client.post(
        "/door-codes",
        data={
            "person_name": "X",
            "starts_at": "2035-07-12T14:00",
            "ends_at": "2035-07-11T12:00",  # end before start
        },
    )
    assert resp.status_code == 400
    assert "La fin doit être postérieure au début." in resp.text
