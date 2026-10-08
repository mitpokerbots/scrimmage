"""Turn an engine gamelog into one hand of a playthrough.

The engine (``game/engine.py``) writes a line-oriented log: ``Round #n`` headers,
one action per line, street lines with a bracketed board, then ``awarded`` lines.
That shape has survived season-to-season rule changes, so the parser follows the
lines instead of this year's streets. Anything it does not recognize is kept as a
note, and the playthrough is a view of the log rather than a second game engine.

Logs are gzipped. Only the requested hand's lines are kept, plus the first hand
so a missing hand number can fall back to it.
"""

from __future__ import annotations

import gzip
import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

EventKind = Literal[
    "blind",
    "deal",
    "fold",
    "call",
    "check",
    "bet",
    "raise",
    "discard",
    "show",
    "award",
    "note",
]

_ROUND = re.compile(r"^Round #(\d+)\b")
_FINAL = re.compile(r"^Final, (\w+) \((-?\d+)\), (\w+) \((-?\d+)\)\s*$")
_BANK = re.compile(r"(\S+) \((-?\d+)\)")
_DEALT = re.compile(r"^(.+?) dealt \[(.*)\]\s*$")
_SHOWS = re.compile(r"^(.+?) shows \[(.*)\]\s*$")
_AWARD = re.compile(r"^(.+?) awarded (-?\d+)\s*$")
_BLIND = re.compile(r"^(.+?) posts the blind of (\d+)\s*$")
_DISCARD = re.compile(r"^(.+?) discards (\S+)\s*$")
_BET = re.compile(r"^(.+?) bets (\d+)\s*$")
_RAISE = re.compile(r"^(.+?) raises to (\d+)\s*$")
_SIMPLE = re.compile(r"^(.+?) (folds|calls|checks)\s*$")
_STREET = re.compile(r"^(.+?) \[(.*)\](.*)$")
_STREET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 ]{0,30}$")

_SUITS = {
    "s": ("♠", "spades", False),
    "h": ("♥", "hearts", True),
    "d": ("♦", "diamonds", True),
    "c": ("♣", "clubs", False),
    "♠": ("♠", "spades", False),
    "♥": ("♥", "hearts", True),
    "♦": ("♦", "diamonds", True),
    "♣": ("♣", "clubs", False),
}
_RANKS = {
    **{str(n): str(n) for n in range(2, 10)},
    "10": "10",
    "t": "10",
    "T": "10",
    "j": "J",
    "J": "J",
    "q": "Q",
    "Q": "Q",
    "k": "K",
    "K": "K",
    "a": "A",
    "A": "A",
}

_MAX_EVENTS = 500
_MAX_PREAMBLE = 40


@dataclass(frozen=True)
class Card:
    raw: str
    rank: str
    suit: str
    suit_name: str
    red: bool


@dataclass(frozen=True)
class Event:
    kind: EventKind
    actor: str
    cards: tuple[Card, ...]
    amount: int | None
    detail: str


@dataclass(frozen=True)
class Street:
    name: str
    board: tuple[Card, ...]
    events: tuple[Event, ...]


@dataclass(frozen=True)
class Hand:
    number: int
    banks: tuple[tuple[str, int], ...]
    hole: tuple[tuple[str, tuple[Card, ...]], ...]
    discarded: tuple[tuple[str, tuple[str, ...]], ...]
    roles: tuple[tuple[str, str], ...]
    streets: tuple[Street, ...]
    awards: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ParsedLog:
    hand: Hand | None
    count: int
    prev_number: int | None
    next_number: int | None
    final: tuple[tuple[str, int], ...] | None
    preamble: tuple[str, ...]
    missing: bool
    error: str | None


@dataclass(frozen=True)
class Seat:
    """One player, ready for the template. ``key`` is the engine's name (A or B)."""

    key: str
    name: str
    role: str
    bot: str
    cards: tuple[Card, ...]
    discarded: frozenset[str]
    bank: int | None
    mine: bool


@dataclass
class _StreetBuild:
    name: str
    board: tuple[Card, ...]
    events: list[Event]


def parse_path(path: Path, hand_number: int | None = None) -> ParsedLog:
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return parse_lines(handle, hand_number)
    except (OSError, EOFError, zlib.error):
        return _failed("The game log could not be read.")


def parse_lines(lines: Iterable[str], hand_number: int | None = None) -> ParsedLog:
    """Parse ``lines``. ``hand_number`` None selects the first hand."""
    preamble: list[str] = []
    numbers: list[int] = []
    final: tuple[tuple[str, int], ...] | None = None
    fallback: list[str] | None = None
    fallback_number: int | None = None
    kept: list[str] | None = None
    kept_number: int | None = None
    collect_fallback = False
    collect_kept = False

    for raw in lines:
        line = raw.rstrip("\n\r")
        final_match = _FINAL.match(line)
        if final_match:
            final = (
                (final_match.group(1), int(final_match.group(2))),
                (final_match.group(3), int(final_match.group(4))),
            )
            continue
        if " bets EV: " in line:
            continue
        round_match = _ROUND.match(line)
        if round_match:
            number = int(round_match.group(1))
            numbers.append(number)
            collect_fallback = False
            collect_kept = False
            if fallback is None:
                fallback = [line]
                fallback_number = number
                collect_fallback = True
            if kept is None and (hand_number is None or number == hand_number):
                kept = fallback if number == fallback_number else [line]
                kept_number = number
                collect_kept = True
            continue
        if not numbers:
            if line.strip() and len(preamble) < _MAX_PREAMBLE:
                preamble.append(line)
            continue
        if not line.strip():
            continue
        # When the requested hand is the first, ``kept`` and ``fallback`` are the
        # same list; append once.
        if collect_fallback and fallback is not None:
            fallback.append(line)
        elif collect_kept and kept is not None:
            kept.append(line)

    if not numbers:
        return ParsedLog(None, 0, None, None, final, tuple(preamble), False, None)

    missing = hand_number is not None and hand_number not in numbers
    show = hand_number if hand_number in numbers else numbers[0]
    if kept is not None and kept_number == show:
        chosen = kept
    elif fallback is not None and fallback_number == show:
        chosen = fallback
    else:
        return _failed("The log listed that hand, but its text could not be read.")
    index = numbers.index(show)
    prev_number = numbers[index - 1] if index > 0 else None
    next_number = numbers[index + 1] if index + 1 < len(numbers) else None
    return ParsedLog(
        hand=_parse_hand(show, chosen),
        count=len(numbers),
        prev_number=prev_number,
        next_number=next_number,
        final=final,
        preamble=tuple(preamble),
        missing=missing,
        error=None,
    )


def seats_for(
    hand: Hand,
    names: Mapping[str, str],
    bots: Mapping[str, str],
    me_key: str | None,
) -> list[Seat]:
    """Players in the hand, with the viewer's seat first."""
    hole = dict(hand.hole)
    roles = dict(hand.roles)
    banks = dict(hand.banks)
    tossed = {name: frozenset(cards) for name, cards in hand.discarded}
    order: list[str] = []
    for name in list(hole) + list(banks):
        if name not in order:
            order.append(name)
    if me_key in order:
        order.remove(me_key)
        order.insert(0, me_key)
    return [
        Seat(
            key=name,
            name=names.get(name, name),
            role=roles.get(name, ""),
            bot=bots.get(name, ""),
            cards=hole.get(name, ()),
            discarded=tossed.get(name, frozenset()),
            bank=banks.get(name),
            mine=name == me_key,
        )
        for name in order
    ]


def hand_summary(hand: Hand, names: Mapping[str, str]) -> str:
    if not hand.awards:
        return "The log does not say who won this hand."
    if all(amount == 0 for _, amount in hand.awards):
        return "This hand was a split pot."
    parts = [
        f"{names.get(actor, actor)} won {_chips(amount)}"
        for actor, amount in hand.awards
        if amount > 0
    ]
    if not parts:
        return "This hand ended."
    return " and ".join(parts) + " this hand."


def _chips(amount: int) -> str:
    return "1 chip" if amount == 1 else f"{amount} chips"


def _failed(message: str) -> ParsedLog:
    return ParsedLog(None, 0, None, None, None, (), False, message)


def _card(token: str) -> Card:
    text = token.strip()
    if len(text) >= 2 and text[-1] in _SUITS and text[:-1] in _RANKS:
        glyph, suit_name, red = _SUITS[text[-1]]
        return Card(text, _RANKS[text[:-1]], glyph, suit_name, red)
    return Card(text, text, "", "", False)


def _cards(text: str) -> tuple[Card, ...]:
    return tuple(_card(part) for part in text.split() if part.strip())


def _event(
    kind: EventKind,
    actor: str,
    cards: tuple[Card, ...] = (),
    amount: int | None = None,
    detail: str = "",
) -> Event:
    return Event(kind, actor, cards, amount, detail)


def _classify(line: str) -> tuple[Event | None, str | None, tuple[Card, ...]]:
    matched = _DEALT.match(line)
    if matched:
        return _event("deal", matched.group(1), _cards(matched.group(2))), None, ()
    matched = _SHOWS.match(line)
    if matched:
        return _event("show", matched.group(1), _cards(matched.group(2))), None, ()
    matched = _AWARD.match(line)
    if matched:
        return _event("award", matched.group(1), (), int(matched.group(2))), None, ()
    matched = _BLIND.match(line)
    if matched:
        return _event("blind", matched.group(1), (), int(matched.group(2))), None, ()
    matched = _DISCARD.match(line)
    if matched:
        token = matched.group(2)
        return _event("discard", matched.group(1), (_card(token),), detail=token), None, ()
    matched = _BET.match(line)
    if matched:
        return _event("bet", matched.group(1), (), int(matched.group(2))), None, ()
    matched = _RAISE.match(line)
    if matched:
        return _event("raise", matched.group(1), (), int(matched.group(2))), None, ()
    matched = _SIMPLE.match(line)
    if matched:
        word = matched.group(2)
        if word == "folds":
            kind: EventKind = "fold"
        elif word == "calls":
            kind = "call"
        else:
            kind = "check"
        return _event(kind, matched.group(1)), None, ()
    matched = _STREET.match(line)
    if matched and _STREET_NAME.match(matched.group(1).strip()):
        return None, matched.group(1).strip(), _cards(matched.group(2))
    return _event("note", "", detail=line), None, ()


def _parse_hand(number: int, lines: list[str]) -> Hand:
    banks = tuple((item.group(1), int(item.group(2))) for item in _BANK.finditer(lines[0]))
    built = [_StreetBuild("Preflop", (), [])]
    hole: dict[str, tuple[Card, ...]] = {}
    tossed: dict[str, list[str]] = {}
    roles: dict[str, str] = {}
    awards: list[tuple[str, int]] = []
    blinds: list[tuple[str, int]] = []
    total = 0
    for line in lines[1:]:
        if total >= _MAX_EVENTS:
            built[-1].events.append(_event("note", "", detail="Further lines omitted."))
            break
        total += 1
        event, street_name, board = _classify(line)
        if street_name is not None:
            built.append(_StreetBuild(street_name, board, []))
            continue
        if event is None:
            continue
        if event.kind == "deal":
            hole[event.actor] = event.cards
        elif event.kind == "discard" and event.cards:
            tossed.setdefault(event.actor, []).append(event.cards[0].raw)
        elif event.kind == "award" and event.amount is not None:
            awards.append((event.actor, event.amount))
        elif event.kind == "blind" and event.amount is not None:
            blinds.append((event.actor, event.amount))
        built[-1].events.append(event)
    if len(blinds) >= 2:
        small_actor, small_amount = min(blinds, key=lambda item: item[1])
        big_actor, big_amount = max(blinds, key=lambda item: item[1])
        if small_actor != big_actor and small_amount != big_amount:
            roles[small_actor] = "Small blind"
            roles[big_actor] = "Big blind"
    return Hand(
        number=number,
        banks=banks,
        hole=tuple(hole.items()),
        discarded=tuple((name, tuple(cards)) for name, cards in tossed.items()),
        roles=tuple(roles.items()),
        streets=tuple(Street(street.name, street.board, tuple(street.events)) for street in built),
        awards=tuple(awards),
    )
