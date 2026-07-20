"""Tests for scripts/payment_reconcile.py — deterministic parsing + matching.

Uses the real e-mail wording seen in the inbox (Banque Populaire "virement
instantané" received/sent notifications). No network / no Beds24 needed.

Runnable two ways:
    pytest tests/test_payment_reconcile.py
    python3 tests/test_payment_reconcile.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import payment_reconcile as pr  # noqa: E402


# ------------------------------------------------------------- money() ------
def test_money_french_formats():
    assert pr.money("4 077,00") == 4077.00
    assert pr.money("16 800,00") == 16800.00
    assert pr.money("1.234,56") == 1234.56
    assert pr.money("450") == 450.0
    assert pr.money("324.94") == 324.94


# ------------------------------------------------------------- BP parse -----
# Real wording of the current BP "virement instantané" notifications.
BP_RECEIVED_SUBJECT = "Réception d'un virement instantané de 324.94"
BP_RECEIVED_BODY = (
    "Virement instantané reçu Bonjour , Nous vous confirmons la réception d'un "
    "virement instantané à votre profit le 06/07/26 à 15:00:20. Information "
    "virement instantané Emetteur : M. SONMEZ MUSTAFA EI Montant : 324.94 "
    "Compte bénéficiaire : 38022XXXX94 Référence : Loyer Motif du virement : "
    "Non renseigné Nature du paiement : Non renseignee Le montant de ce virement "
    "a bien été crédité sur votre compte. Bien cordialement, Banque Populaire "
    "Auvergne Rhône Alpes"
)

BP_SENT_SUBJECT = "Exécution de votre virement instantané de 610.00"
BP_SENT_BODY = (
    "Virement instantané émis Bonjour , Nous vous confirmons la réalisation de "
    "votre ordre de virement instantané du 04/07/26 à 19:07:09. Information "
    "virement instantané Compte bénéficiaire : 00021XXXX01 Nom bénéficiaire : "
    "GUILHERME VELOSO Virgini Montant : 610.00 Référence : Virement de M "
    "MARTINSSON JOHAN OU M Ce montant a bien été crédité sur le compte de votre "
    "bénéficiaire. Bien cordialement, Banque Populaire Auvergne Rhône Alpes"
)


def test_parse_bp_received():
    c = pr._parse_bp(BP_RECEIVED_SUBJECT, BP_RECEIVED_BODY, "2026-07-13")
    assert c is not None
    assert c.amount == 324.94
    assert c.payer == "M. SONMEZ MUSTAFA EI"
    assert c.reference == "Loyer"
    assert c.date == "2026-07-06"
    assert c.source == "BP"


def test_parse_bp_sent_is_ignored():
    # An outgoing transfer must NEVER produce a credit for reconciliation.
    assert pr._parse_bp(BP_SENT_SUBJECT, BP_SENT_BODY, "2026-07-13") is None


# --------------------------------------------------------- direct filter ----
def test_is_direct():
    assert pr.Booking(1, "X", "2026-07-20", "Direct", "new", 100, 0).is_direct
    assert pr.Booking(1, "X", "2026-07-20", "", "new", 100, 0).is_direct
    assert not pr.Booking(1, "X", "2026-07-20", "Airbnb", "new", 100, 0).is_direct
    assert not pr.Booking(1, "X", "2026-07-20", "Booking.com", "new", 100, 0).is_direct


# ------------------------------------------------------------- matching -----
def _bk(guest, arrival, charges, payments=0.0, referer="Direct"):
    return pr.Booking(id=hash(guest) & 0xffff, guest=guest, arrival=arrival,
                      referer=referer, status="new", charges=charges, payments=payments)


def test_reconcile_paid_wrong_and_unpaid():
    bookings = [
        _bk("Marine Cuenot", "2026-07-16", 450.00),      # exact transfer -> PAID
        _bk("Arnaud Phalip", "2026-07-17", 600.00),      # wrong amount -> WRONG_AMOUNT
        _bk("Lennart Johann", "2026-07-18", 800.00),     # nothing -> UNPAID
        _bk("Guest ViaAirbnb", "2026-07-16", 300.00, referer="Airbnb"),  # ignored
        _bk("Paid Already", "2026-07-16", 300.00, payments=300.00),      # balance 0 -> ignored
    ]
    credits = [
        pr.Credit(450.00, "Marine Cuenot", "sejour juillet", "2026-07-12", "BP"),
        pr.Credit(500.00, "Arnaud Phalip", "acompte", "2026-07-11", "BP"),
    ]
    results = {r.booking.guest: r for r in pr.reconcile(bookings, credits, alert_days=7)}

    assert set(results) == {"Marine Cuenot", "Arnaud Phalip", "Lennart Johann"}
    assert results["Marine Cuenot"].status == "PAID"
    assert results["Arnaud Phalip"].status == "WRONG_AMOUNT"
    assert "600.00" in results["Arnaud Phalip"].note
    assert results["Lennart Johann"].status == "UNPAID"


def test_amount_match_without_name_still_pays():
    # A transfer with an unhelpful label but the exact balance still matches.
    bookings = [_bk("Sophie Durand", "2026-07-20", 375.50)]
    credits = [pr.Credit(375.50, "MR DURAND SOPHIE", "VIR SEPA", "2026-07-15", "BP")]
    r = pr.reconcile(bookings, credits, alert_days=7)[0]
    assert r.status == "PAID"


def test_one_credit_not_reused_for_two_bookings():
    bookings = [_bk("A One", "2026-07-20", 200.0), _bk("B Two", "2026-07-21", 200.0)]
    credits = [pr.Credit(200.0, "A One", "", "2026-07-15", "BP")]
    res = {r.booking.guest: r.status for r in pr.reconcile(bookings, credits, 7)}
    assert res["A One"] == "PAID"
    assert res["B Two"] == "UNPAID"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
