from __future__ import annotations

import gzip
from pathlib import Path

from scrimmage.web.gamelog import hand_summary, parse_lines, parse_path, seats_for

LOG = """\
6.9630 MIT Pokerbots - A vs B

Round #1, A (0), B (0)
A posts the blind of 1
B posts the blind of 2
A dealt [Ah Kd Zz]
B dealt [Qs Jh 2d]
A calls
B checks
Flop [Th 9c], A (2), B (2)
Current stacks: 398, 398
B discards Qs
A checks
Discard 1 [Th 9c Qs], A (2), B (2)
A ran out of time
A discards Zz
B checks
A awarded 4
B awarded -4

Round #2, B (-4), A (4)
B posts the blind of 1
A posts the blind of 2
B dealt [2c 2d 2h]
A dealt [As Ks Qs]
B folds
B awarded -1
A awarded 1

Final, A (5), B (-5)
A preflop bets EV: 1
B flop bets EV: 0
"""

NAMES = {"A": "Aces", "B": "Kings"}


def test_first_hand_parses_cards_actions_and_positions() -> None:
    parsed = parse_lines(LOG.splitlines(), None)
    assert parsed.error is None
    assert parsed.count == 2
    assert parsed.prev_number is None
    assert parsed.next_number == 2
    assert parsed.missing is False
    assert parsed.final == (("A", 5), ("B", -5))
    assert parsed.preamble == ("6.9630 MIT Pokerbots - A vs B",)
    hand = parsed.hand
    assert hand is not None and hand.number == 1
    hole = dict(hand.hole)
    assert [card.rank for card in hole["A"]] == ["A", "K", "Zz"]
    assert hole["A"][0].suit == "♥" and hole["A"][0].red
    assert hole["A"][2].suit == ""
    flop = hand.streets[1]
    assert flop.name == "Flop"
    assert flop.board[0].rank == "10" and flop.board[0].suit_name == "hearts"
    assert dict(hand.discarded)["B"] == ("Qs",)
    assert dict(hand.roles) == {"A": "Small blind", "B": "Big blind"}
    assert hand.awards == (("A", 4), ("B", -4))
    notes = [
        event.detail
        for street in hand.streets
        for event in street.events
        if event.kind == "note"
    ]
    assert "A ran out of time" in notes
    assert "Current stacks: 398, 398" in notes
    assert not any("bets EV" in event.detail for street in hand.streets for event in street.events)
    assert hand_summary(hand, NAMES) == "Aces won 4 chips this hand."


def test_second_hand_and_missing_number() -> None:
    second = parse_lines(LOG.splitlines(), 2)
    assert second.hand is not None
    assert second.hand.number == 2
    assert second.prev_number == 1 and second.next_number is None
    assert dict(second.hand.roles)["B"] == "Small blind"
    assert [card.raw for card in dict(second.hand.hole)["B"]] == ["2c", "2d", "2h"]
    assert hand_summary(second.hand, NAMES) == "Aces won 1 chip this hand."
    kinds = [event.kind for street in second.hand.streets for event in street.events]
    assert "fold" in kinds and "show" not in kinds

    missing = parse_lines(LOG.splitlines(), 9)
    assert missing.missing and missing.hand is not None and missing.hand.number == 1
    assert missing.next_number == 2


def test_empty_log_and_bad_gzip(tmp_path: Path) -> None:
    assert parse_lines([], None).hand is None
    assert parse_lines(["not a poker log"], None).preamble == ("not a poker log",)

    path = tmp_path / "game.log.gz"
    path.write_bytes(gzip.compress(LOG.encode()))
    loaded = parse_path(path, 1)
    assert loaded.hand is not None and loaded.hand.number == 1

    path.write_bytes(b"this is not gzip")
    broken = parse_path(path, None)
    assert broken.hand is None and broken.error


def test_viewer_seat_is_listed_first() -> None:
    parsed = parse_lines(LOG.splitlines(), 1)
    assert parsed.hand is not None
    seats = seats_for(parsed.hand, NAMES, {"A": "good", "B": "evil"}, "A")
    assert [seat.name for seat in seats] == ["Aces", "Kings"]
    assert seats[0].mine and seats[0].role == "Small blind" and seats[0].bot == "good"
    assert seats[0].bank == 0
    assert "Zz" in seats[0].discarded
    assert "Qs" in seats[1].discarded
