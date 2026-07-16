"""
Lightweight two-language (English / French) localisation for the owner console.

The site is a set of hand-built HTML pages, so there is no template engine to
hook a gettext catalogue into. Instead every user-facing string lives in the
`_STRINGS` table below keyed by a short id, and each page renders through a
`Translator` bound to the request's language.

Language resolution (see `lang_from_request`):
  1. an explicit `lang` cookie (set by the FR/EN switcher) wins;
  2. otherwise the browser's `Accept-Language` header, first supported tag;
  3. otherwise `DEFAULT_LANG` (English).

Keeping English the fallback means the switcher is purely additive — a French
owner's browser already asks for French and gets it, everyone else keeps the
original pages, and either can flip languages explicitly at any time.
"""

from __future__ import annotations

from fastapi import Request

DEFAULT_LANG = "en"
LANGUAGES = ("en", "fr")
LANG_COOKIE = "lang"
# Remember the chosen language for a year, like the login session.
LANG_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def normalize_lang(value: str | None) -> str | None:
    """Map a raw language string (cookie value or Accept-Language tag) to a
    supported code, or None if it isn't one we handle."""
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("fr"):
        return "fr"
    if v.startswith("en"):
        return "en"
    return None


def lang_from_request(request: Request) -> str:
    """Resolve the UI language: cookie → Accept-Language → default."""
    cookie = normalize_lang(request.cookies.get(LANG_COOKIE))
    if cookie:
        return cookie
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        tag = normalize_lang(part.split(";")[0])
        if tag:
            return tag
    return DEFAULT_LANG


class Translator:
    """Callable that renders a string id in a fixed language.

    Usage: ``t = Translator("fr"); t("login.submit")``. Missing keys fall back
    to the English string, then to the raw key, so a forgotten translation
    degrades visibly rather than crashing. Positional/format args are supported
    via ``t("early.msg_to", name="Alice", lang="French")``.
    """

    def __init__(self, lang: str):
        self.lang = lang if lang in LANGUAGES else DEFAULT_LANG

    def __call__(self, key: str, **kwargs: object) -> str:
        entry = _STRINGS.get(key)
        if entry is None:
            text = key
        else:
            text = entry.get(self.lang) or entry.get(DEFAULT_LANG) or key
        return text.format(**kwargs) if kwargs else text


def translator_for(request: Request) -> Translator:
    return Translator(lang_from_request(request))


# --- string catalogue -------------------------------------------------------
# key -> {"en": ..., "fr": ...}. Group by page for readability.
_STRINGS: dict[str, dict[str, str]] = {
    # Shared footer navigation + common bits reused across pages.
    "nav.free_nights": {"en": "Free nights", "fr": "Nuits libres"},
    "nav.early_checkin": {"en": "Early check-in", "fr": "Arrivée anticipée"},
    "nav.adhoc_code": {"en": "Ad-hoc code", "fr": "Code ponctuel"},
    "nav.drafts": {"en": "Drafts", "fr": "Brouillons"},
    "nav.logout": {"en": "Logout", "fr": "Déconnexion"},
    "common.property": {"en": "Property", "fr": "Logement"},
    "common.create_code": {"en": "Create code", "fr": "Créer le code"},
    "common.code_created": {"en": "Code created", "fr": "Code créé"},
    "common.for": {"en": "For", "fr": "Pour"},
    "common.property_label": {"en": "Property", "fr": "Logement"},
    "common.valid": {"en": "Valid", "fr": "Valable"},
    "lang.french": {"en": "French", "fr": "français"},
    "lang.english": {"en": "English", "fr": "anglais"},
    # Copy-to-clipboard button (layout.code_result).
    "copy.button": {"en": "Copy code", "fr": "Copier le code"},
    "copy.done": {"en": "Copied ✓", "fr": "Copié ✓"},
    # Login page.
    "login.title": {"en": "Login — Check-in", "fr": "Connexion — Check-in"},
    "login.heading": {"en": "Check-in Console", "fr": "Console Check-in"},
    "login.subtitle": {"en": "Owner sign-in", "fr": "Connexion propriétaire"},
    "login.user": {"en": "User", "fr": "Utilisateur"},
    "login.password": {"en": "Password", "fr": "Mot de passe"},
    "login.submit": {"en": "Sign in", "fr": "Se connecter"},
    "login.error": {
        "en": "Wrong user or password.",
        "fr": "Utilisateur ou mot de passe incorrect.",
    },
    # Ad-hoc door-code page.
    "door.title": {"en": "Create Door Code", "fr": "Créer un code d'accès"},
    "door.heading": {"en": "Create a door code", "fr": "Créer un code d'accès"},
    "door.subtitle": {
        "en": "Temporary Igloohome code via Make",
        "fr": "Code Igloohome temporaire via Make",
    },
    "door.default_lock": {"en": "— Default lock —", "fr": "— Serrure par défaut —"},
    "door.for_whom": {"en": "For whom (optional)", "fr": "Pour qui (facultatif)"},
    "door.for_whom_ph": {
        "en": "e.g. Plombier Dupont — just a label",
        "fr": "ex. Plombier Dupont — simple étiquette",
    },
    "door.valid_from": {"en": "Valid from", "fr": "Valable à partir du"},
    "door.valid_until": {"en": "Valid until", "fr": "Valable jusqu'au"},
    "door.hint": {
        "en": "Igloohome codes start and end on the hour — minutes are rounded "
        "(start down, end up).",
        "fr": "Les codes Igloohome commencent et se terminent à l'heure pile — "
        "les minutes sont arrondies (début vers le bas, fin vers le haut).",
    },
    "door.result_title": {"en": "Door Code Created", "fr": "Code d'accès créé"},
    "door.create_another": {"en": "Create another", "fr": "En créer un autre"},
    # Early check-in page.
    "early.title": {"en": "Early check-in", "fr": "Arrivée anticipée"},
    "early.heading": {"en": "Early check-in code", "fr": "Code d'arrivée anticipée"},
    "early.subtitle": {
        "en": "Create a code for a guest and send it",
        "fr": "Créer un code pour un voyageur et le lui envoyer",
    },
    "early.reservation": {"en": "Reservation", "fr": "Réservation"},
    "early.start_hour": {"en": "Start hour", "fr": "Heure de début"},
    "early.end_hour": {"en": "End hour", "fr": "Heure de fin"},
    "early.select_property": {"en": "— Select property —", "fr": "— Choisir un logement —"},
    "early.select_property_first": {
        "en": "— Select property first —",
        "fr": "— Choisir d'abord un logement —",
    },
    "early.select_reservation": {
        "en": "— Select reservation —",
        "fr": "— Choisir une réservation —",
    },
    "early.no_reservations": {
        "en": "No upcoming reservations",
        "fr": "Aucune réservation à venir",
    },
    "early.valid_from": {"en": "Valid from", "fr": "Valable à partir du"},
    "early.valid_until": {"en": "Valid until", "fr": "Valable jusqu'au"},
    "early.hint": {
        "en": "Date fills in from the reservation — for an early check-in just "
        "change the start hour. Defaults: from 14:00 → until 12:00.",
        "fr": "La date se remplit depuis la réservation — pour une arrivée "
        "anticipée, changez seulement l'heure de début. Par défaut : de 14:00 "
        "→ jusqu'à 12:00.",
    },
    "early.result_title": {
        "en": "Early check-in — code created",
        "fr": "Arrivée anticipée — code créé",
    },
    "early.sent_title": {"en": "Early check-in — sent", "fr": "Arrivée anticipée — envoyé"},
    "early.send_title": {"en": "Early check-in — send", "fr": "Arrivée anticipée — envoi"},
    "early.error_title": {"en": "Early check-in — error", "fr": "Arrivée anticipée — erreur"},
    "early.no_backend": {
        "en": "Reservations unavailable — no booking backend is configured on "
        "this server.",
        "fr": "Réservations indisponibles — aucun système de réservation n'est "
        "configuré sur ce serveur.",
    },
    "early.load_failed": {
        "en": "Could not load {source} reservations: {exc}",
        "fr": "Impossible de charger les réservations {source} : {exc}",
    },
    "early.another_guest": {"en": "Another guest", "fr": "Autre voyageur"},
    "early.msg_to": {"en": "Message to {name} ({lang})", "fr": "Message pour {name} ({lang})"},
    "early.send_to_guest": {"en": "Send to guest", "fr": "Envoyer au voyageur"},
    "early.sent_heading": {"en": "Sent", "fr": "Envoyé"},
    "early.sent_body": {
        "en": "Message sent to {name} ✓",
        "fr": "Message envoyé à {name} ✓",
    },
    "early.the_guest": {"en": "the guest", "fr": "le voyageur"},
    "early.send_heading": {"en": "Send the code", "fr": "Envoyer le code"},
    "early.start_over": {"en": "Start over", "fr": "Recommencer"},
    "early.couldnt_send": {"en": "Couldn't send", "fr": "Envoi impossible"},
    "early.back": {"en": "Back to early check-in", "fr": "Retour à l'arrivée anticipée"},
    # Errors shared by the door-code and early check-in flows.
    "err.no_gateway": {
        "en": "Door lock gateway is not configured (set MAKE_IGLOOHOME_WEBHOOK_URL).",
        "fr": "La passerelle de serrure n'est pas configurée "
        "(définir MAKE_IGLOOHOME_WEBHOOK_URL).",
    },
    "err.bad_datetime": {
        "en": "Invalid date/time format.",
        "fr": "Format de date/heure invalide.",
    },
    "err.end_before_start": {
        "en": "The end must be after the start.",
        "fr": "La fin doit être postérieure au début.",
    },
    "err.window_over": {
        "en": "That window is already over — pick an end time in the future.",
        "fr": "Cette plage est déjà terminée — choisissez une fin dans le futur.",
    },
    "err.create_failed": {
        "en": "Code creation failed: {exc}",
        "fr": "Échec de la création du code : {exc}",
    },
    "err.select_property": {"en": "Select a property.", "fr": "Choisissez un logement."},
    "err.select_reservation": {
        "en": "Select a reservation.",
        "fr": "Choisissez une réservation.",
    },
    "err.missing_reservation": {
        "en": "Missing reservation — go back and create the code again.",
        "fr": "Réservation manquante — revenez en arrière et recréez le code.",
    },
    "err.empty_message": {"en": "The message is empty.", "fr": "Le message est vide."},
    "err.backend_not_configured": {
        "en": "The {source} backend is not configured — cannot send.",
        "fr": "Le système {source} n'est pas configuré — envoi impossible.",
    },
    "err.send_failed": {"en": "Sending failed: {exc}", "fr": "Échec de l'envoi : {exc}"},
    # Draft review page.
    "review.title": {"en": "Draft Review", "fr": "Revue des brouillons"},
    "review.heading": {"en": "Pending Drafts", "fr": "Brouillons en attente"},
    "review.empty": {"en": "No pending drafts.", "fr": "Aucun brouillon en attente."},
    "review.guest_request": {"en": "Guest request", "fr": "Demande du voyageur"},
    "review.guest": {"en": "Guest", "fr": "Voyageur"},
    "review.email_sent": {"en": "Email sent to cleaner", "fr": "E-mail envoyé au ménage"},
    "review.date": {"en": "date", "fr": "date"},
    "review.cleaner_reply": {"en": "Cleaner reply", "fr": "Réponse du ménage"},
    "review.context": {"en": "Context", "fr": "Contexte"},
    "review.proposed": {
        "en": "Proposed reply to guest",
        "fr": "Réponse proposée au voyageur",
    },
    "review.approve": {"en": "Approve &amp; Send", "fr": "Approuver et envoyer"},
    "review.why_rejected": {"en": "Why rejected?", "fr": "Motif du refus ?"},
    "review.reject": {"en": "Reject", "fr": "Refuser"},
    "review.draft": {"en": "Draft", "fr": "Brouillon"},
    "review.reservation_word": {"en": "reservation", "fr": "réservation"},
}
