"""
Tests for the /occupancy free-night grid.

Covers the free/booked logic that the whole screen exists for: a night reads as
free only when no stay covers it (including stays that began before the window),
per-property and per-day counts, and single-night orphan detection.
"""

import os

from fastapi.testclient import TestClient

from src.ports.reservations import BookingGatewayError, GuestBookingGateway, Reservation
from src.web.app import create_app
from src.web.occupancy import _build_grid


class FakeOverlapGateway(GuestBookingGateway):
    def __init__(self, reservations=None, fail=False, managed=None, changes=None):
        self._res = reservations or []
        self._fail = fail
        self._managed = managed or []
        self._changes = changes

    async def upcoming_arrivals(self, days: int):
        return list(self._res)

    async def stays_overlapping(self, start, end):
        if self._fail:
            raise BookingGatewayError("boom")
        return list(self._res)

    async def bookings_changed_since(self, since):
        if self._changes is None:
            raise NotImplementedError  # like a gateway without change support
        return list(self._changes)

    async def send_guest_message(self, booking_id: int, message: str) -> None:
        pass

    def managed_properties(self):
        return list(self._managed)


def _make_client(gateway=None, smoobu_gateway=None):
    os.environ.setdefault("REVIEW_TOKEN", "test-token")
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_USER", "test@test.com")
    os.environ.setdefault("SMTP_PASSWORD", "x")
    os.environ.setdefault("IMAP_HOST", "localhost")
    os.environ.setdefault("CLEANER_EMAIL", "cleaner@test.com")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    app = create_app()
    app.state.booking_gateway = gateway
    app.state.smoobu_gateway = smoobu_gateway
    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


# --- unit tests for the grid logic ----------------------------------------

def _dates(start_iso, n):
    from datetime import date, timedelta

    y, m, d = (int(x) for x in start_iso.split("-"))
    s = date(y, m, d)
    return [s + timedelta(days=i) for i in range(n)]


def test_night_is_free_only_when_no_stay_covers_it():
    days = _dates("2026-07-16", 5)  # 16,17,18,19,20
    # Guest sleeps 16 & 17 (arrival 16, departure 18); 18,19,20 free.
    res = [Reservation(booking_id=1, property_id=100, guest_name="A", arrival="2026-07-16", departure="2026-07-18")]
    rows, per_day_free, grand = _build_grid([("Flat", 100)], days, {100: res})
    _, cells, free_count = rows[0]
    assert [free for free, _ in cells] == [False, False, True, True, True]
    assert free_count == 3
    assert per_day_free == [0, 0, 1, 1, 1]
    assert grand == 3


def test_stay_in_progress_before_window_counts_as_booked():
    """A stay that started before the window (arrival < start) still occupies its
    nights — the exact case upcoming_arrivals would miss."""
    days = _dates("2026-07-16", 4)
    res = [Reservation(booking_id=9, property_id=100, guest_name="Long", arrival="2026-07-10", departure="2026-07-25")]
    rows, per_day_free, grand = _build_grid([("Flat", 100)], days, {100: res})
    _, cells, free_count = rows[0]
    assert all(not free for free, _ in cells)  # fully booked, nothing free
    assert free_count == 0 and grand == 0


def test_orphan_is_a_single_free_night_between_two_booked_nights():
    days = _dates("2026-07-16", 4)  # 16,17,18,19
    # Booked 16, free 17, booked 18 -> 17 is an orphan. 19 free (edge, not orphan).
    res = [
        Reservation(booking_id=1, property_id=100, guest_name="A", arrival="2026-07-16", departure="2026-07-17"),
        Reservation(booking_id=2, property_id=100, guest_name="B", arrival="2026-07-18", departure="2026-07-19"),
    ]
    rows, _, _ = _build_grid([("Flat", 100)], days, {100: res})
    _, cells, _ = rows[0]
    orphans = [orphan for _, orphan in cells]
    assert orphans == [False, True, False, False]  # only index 1 (the 17th)


def test_edge_free_night_is_not_an_orphan():
    days = _dates("2026-07-16", 3)  # free 16, booked 17,18
    res = [Reservation(booking_id=1, property_id=100, guest_name="A", arrival="2026-07-17", departure="2026-07-19")]
    rows, _, _ = _build_grid([("Flat", 100)], days, {100: res})
    _, cells, _ = rows[0]
    assert [orphan for _, orphan in cells] == [False, False, False]  # index 0 has no left neighbour


# --- route/integration tests ----------------------------------------------

def test_page_renders_grid_with_all_properties_and_totals():
    res = [
        Reservation(booking_id=1, property_id=326275, guest_name="Costa", arrival="2026-07-15", departure="2026-07-17"),
    ]
    client = _make_client(gateway=FakeOverlapGateway(res))
    resp = client.get("/occupancy?days=7")
    assert resp.status_code == 200
    # every Beds24 flat appears as a row
    for name in ("Velours T2", "La Palma", "Le Fernand"):
        assert name in resp.text
    assert "Free nights" in resp.text
    assert "free / day" in resp.text  # per-day footer present


def test_smoobu_property_row_is_added_from_managed_properties():
    smoobu = FakeOverlapGateway([], managed=[("Hippocrate", 3230512)])
    client = _make_client(gateway=FakeOverlapGateway([]), smoobu_gateway=smoobu)
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Hippocrate" in resp.text


def test_days_param_is_clamped():
    client = _make_client(gateway=FakeOverlapGateway([]))
    assert client.get("/occupancy?days=999").status_code == 200   # clamped, no crash
    assert client.get("/occupancy?days=notanumber").status_code == 200  # falls back to default


def test_gateway_failure_shows_note_but_still_renders():
    client = _make_client(gateway=FakeOverlapGateway(fail=True))
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Could not load" in resp.text


def test_renders_in_french_when_language_is_french():
    client = _make_client(gateway=FakeOverlapGateway([]))
    client.cookies.set("lang", "fr")
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Nuits libres" in resp.text          # heading/title localized
    assert "libres / jour" in resp.text          # per-day footer localized
    assert "Réservé" in resp.text                # legend localized
    assert 'lang="fr"' in resp.text              # document language attribute


def test_requires_auth():
    client = _make_client(gateway=FakeOverlapGateway([]))
    client.cookies.clear()
    resp = client.get("/occupancy", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)  # redirected to login


# --- stays timeline + changes feed (options D + F) --------------------------

from datetime import date as _date, datetime as _dt, timedelta as _td

from src.web.occupancy import _change_kind, _fresh_cells


def _iso(days_from_now: int) -> str:
    return (_date.today() + _td(days=days_from_now)).isoformat()


def _ts(hours_ago: int) -> str:
    return (_dt.now() - _td(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_change_kind_classification():
    since = _dt.now() - _td(days=7)
    cancelled = Reservation(booking_id=1, property_id=1, guest_name="A",
                            arrival=_iso(3), departure=_iso(6), status="cancelled",
                            booking_time=_ts(3))
    created_in_window = Reservation(booking_id=2, property_id=1, guest_name="B",
                                    arrival=_iso(3), departure=_iso(6), status="confirmed",
                                    booking_time=_ts(5))
    old_but_touched = Reservation(booking_id=3, property_id=1, guest_name="C",
                                  arrival=_iso(3), departure=_iso(6), status="confirmed",
                                  booking_time="2025-01-01T10:00:00", modified_time=_ts(5))
    assert _change_kind(cancelled, since) == "cancel"
    assert _change_kind(created_in_window, since) == "new"
    assert _change_kind(old_but_touched, since) == "mod"


def test_fresh_cells_cover_the_released_nights_only():
    days = [(_date.today() + _td(days=i)) for i in range(5)]
    ghost = Reservation(booking_id=9, property_id=100, guest_name="Gone",
                        arrival=_iso(1), departure=_iso(3), status="cancelled",
                        modified_time=_ts(2))
    cells = _fresh_cells([ghost], days)
    assert cells == {(100, _iso(1)), (100, _iso(2))}  # nights 1 & 2, not departure day


def test_page_shows_stays_timeline_with_guest_names():
    res = [
        Reservation(booking_id=1, property_id=326275, guest_name="Klein",
                    arrival=_iso(0), departure=_iso(2), channel="airbnb", status="confirmed"),
    ]
    client = _make_client(gateway=FakeOverlapGateway(res, changes=[]))
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Stays" in resp.text
    assert "Klein" in resp.text            # bar carries the guest name
    assert "oc-tl-bar" in resp.text
    assert "oc-ch airbnb" in resp.text     # channel dot


def test_recent_cancellation_shows_ghost_feed_entry_and_fresh_cells():
    ghost = Reservation(booking_id=9, property_id=326123, guest_name="Rossi",
                        arrival=_iso(2), departure=_iso(5), channel="booking",
                        status="cancelled", booking_time="2026-06-01T10:00:00",
                        modified_time=_ts(3), price=285.0)
    client = _make_client(gateway=FakeOverlapGateway([], changes=[ghost]))
    client.cookies.set("lang", "fr")
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Annulé" in resp.text                 # feed badge
    assert "oc-tl-ghost" in resp.text            # dashed ghost bar in the timeline
    assert "remise" in resp.text                 # "nuits remises en vente" chip
    assert 'class="cell free fresh' in resp.text  # ✦ cells in the grid
    assert "Libérée récemment" in resp.text      # legend entry appears


def test_new_booking_gets_ring_and_feed_entry():
    fresh_booking = Reservation(booking_id=5, property_id=328510, guest_name="Novak",
                                arrival=_iso(3), departure=_iso(6), channel="airbnb",
                                status="confirmed", booking_time=_ts(4), price=412.0)
    client = _make_client(gateway=FakeOverlapGateway([fresh_booking], changes=[fresh_booking]))
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "is-new" in resp.text     # ring on the timeline bar
    assert ">New<" in resp.text      # feed badge
    assert "412" in resp.text        # price shown in the feed line


def test_quiet_week_renders_empty_feed_note():
    client = _make_client(gateway=FakeOverlapGateway([], changes=[]))
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "No changes in the last" in resp.text


def test_gateway_without_change_support_still_renders():
    client = _make_client(gateway=FakeOverlapGateway([]))  # changes=None -> NotImplementedError
    resp = client.get("/occupancy")
    assert resp.status_code == 200
    assert "Free nights" in resp.text
