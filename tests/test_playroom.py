"""Playroom rooms API.

The rules under test are the ones a client could otherwise cheat: whose turn it
is, which numbers are free, whether a bingo claim is good, and which boards a
caller may see. Every one of them is checked against the server's own state,
never against anything the request carries.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select as sa_select

from app.apps.playroom import models, ratelimit
from app.apps.playroom.bingo import CARD_SIZE, find_winning_lines
from app.apps.playroom.catalogue import CATALOGUE
from app.apps.playroom.keys import ROOM_KEY_ALPHABET, ROOM_KEY_LENGTH
from app.apps.playroom.service import TURN_SECONDS

BASE = "/games/v1"

DEFAULT_SETTINGS = {"rounds": 1, "privacy": "Locked after start", "maxPlayers": 8}


@pytest.fixture(autouse=True)
def fresh_limits():
    """Rate limits are process-global and every test shares one address.

    Without this the eleventh room a test creates is refused, and the failure
    lands on whichever test happens to run eleventh.
    """
    for limiter in ratelimit.ALL_LIMITERS:
        limiter.reset()


@pytest.fixture(autouse=True)
async def catalogue(setup_db, db_session):
    """Seeds the game catalogue.

    The tests build the schema with `create_all`, not with Alembic, so the rows
    migration `a1f3c7d90b21` inserts are not there. Seeding from
    `app.apps.playroom.catalogue` is what keeps the fixture and the migration
    saying the same thing.
    """
    async with db_session() as db:
        for row in CATALOGUE:
            db.add(models.Game(**row))
        await db.commit()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_room(client, name="Rhea", color="peach", settings=None):
    response = await client.post(
        f"{BASE}/rooms",
        json={
            "gameId": "bingo",
            "settings": settings or DEFAULT_SETTINGS,
            "hostName": name,
            "hostColor": color,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def join_room(client, key, name, color="mint"):
    response = await client.post(f"{BASE}/rooms/{key}/players", json={"name": name, "color": color})
    assert response.status_code == 201, response.text
    return response.json()


async def select(client, key, token, value, headers=None):
    # A test takes twenty-five turns in a few milliseconds. No player can, so
    # the per-second action limit would fire on the test loop and on nothing
    # real. It has its own test below.
    ratelimit.action_limiter.reset()
    return await client.post(
        f"{BASE}/rooms/{key}/rounds/current/actions",
        json={"type": "select_number", "payload": {"value": value}},
        headers={**auth(token), **(headers or {})},
    )


async def claim(client, key, token):
    ratelimit.action_limiter.reset()
    return await client.post(
        f"{BASE}/rooms/{key}/rounds/current/actions",
        json={"type": "claim_bingo", "payload": {}},
        headers=auth(token),
    )


async def whose_turn(client, key, token):
    """The player id the server says is on turn."""
    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    bingo = room["bingo"]
    return bingo["turnOrder"][bingo["currentTurnIndex"]]


# --- catalogue and creation ------------------------------------------------


async def test_catalogue_marks_only_bingo_playable(client):
    response = await client.get(f"{BASE}/games")
    assert response.status_code == 200
    by_id = {game["id"]: game for game in response.json()}
    assert by_id["bingo"]["status"] == "playable"
    assert by_id["scribble"]["status"] == "building"


async def test_create_room_returns_key_token_and_host(client):
    created = await create_room(client)
    room = created["room"]

    assert len(room["key"]) == ROOM_KEY_LENGTH
    assert all(character in ROOM_KEY_ALPHABET for character in room["key"])
    assert room["phase"] == "lobby"
    assert room["bingo"] is None
    assert room["lastRound"] is None
    assert room["players"][0]["name"] == "Rhea"
    assert room["players"][0]["initial"] == "R"
    assert room["players"][0]["isHost"] is True
    assert room["hostId"] == created["playerId"]
    assert created["playerToken"]


async def test_a_room_payload_never_carries_a_token(client):
    """The whole point of splitting the id from the credential."""
    created = await create_room(client)
    body = (await client.get(f"{BASE}/rooms/{created['room']['key']}")).text
    assert created["playerToken"] not in body


async def test_cannot_open_a_room_for_a_game_with_no_engine(client):
    response = await client.post(
        f"{BASE}/rooms",
        json={
            "gameId": "scribble",
            "settings": DEFAULT_SETTINGS,
            "hostName": "Rhea",
            "hostColor": "peach",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wrong-phase"


async def test_unknown_key_is_room_not_found(client):
    response = await client.get(f"{BASE}/rooms/ZZZZZZ")
    assert response.status_code == 404
    assert response.json()["code"] == "room-not-found"


async def test_a_blank_nickname_is_rejected(client):
    response = await client.post(
        f"{BASE}/rooms",
        json={
            "gameId": "bingo",
            "settings": DEFAULT_SETTINGS,
            "hostName": "   ",
            "hostColor": "peach",
        },
    )
    assert response.status_code == 422


# --- joining ---------------------------------------------------------------


async def test_join_adds_a_player_and_mints_a_separate_token(client):
    created = await create_room(client)
    key = created["room"]["key"]
    joined = await join_room(client, key, "Dev")

    assert joined["playerId"] != created["playerId"]
    assert joined["playerToken"] != created["playerToken"]
    assert len(joined["room"]["players"]) == 2


async def test_the_key_is_case_insensitive(client):
    created = await create_room(client)
    lowered = created["room"]["key"].lower()
    assert (await client.get(f"{BASE}/rooms/{lowered}")).status_code == 200


async def test_a_duplicate_nickname_is_refused_whatever_the_case(client):
    created = await create_room(client, name="Rhea")
    response = await client.post(
        f"{BASE}/rooms/{created['room']['key']}/players",
        json={"name": "rHeA", "color": "mint"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "name-taken"


async def test_a_full_room_refuses_the_next_player(client):
    created = await create_room(client, settings={**DEFAULT_SETTINGS, "maxPlayers": 2})
    key = created["room"]["key"]
    await join_room(client, key, "Dev")

    response = await client.post(
        f"{BASE}/rooms/{key}/players", json={"name": "Late", "color": "yellow"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "room-full"


async def test_a_locked_room_refuses_a_player_once_play_starts(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    response = await client.post(
        f"{BASE}/rooms/{key}/players", json={"name": "Late", "color": "mint"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "room-locked"


# --- identity --------------------------------------------------------------


async def test_a_token_from_another_room_is_not_in_this_room(client):
    first = await create_room(client, name="Rhea")
    second = await create_room(client, name="Ada")

    response = await client.post(
        f"{BASE}/rooms/{second['room']['key']}/rounds",
        headers=auth(first["playerToken"]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "not-in-room"


async def test_knowing_a_player_id_does_not_let_you_act_as_them(client):
    """The id is public; the credential is not. Sending the id proves nothing."""
    created = await create_room(client)
    key = created["room"]["key"]
    joined = await join_room(client, key, "Dev")

    response = await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(created["playerId"]))
    assert response.status_code == 403

    # And a real player cannot borrow the host's public id to become host.
    response = await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(joined["playerToken"]))
    assert response.status_code == 403
    assert response.json()["code"] == "not-host"


# --- board visibility ------------------------------------------------------


async def test_a_player_sees_only_their_own_board(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(host_token))).json()
    assert set(room["bingo"]["cards"]) == {created["playerId"]}
    assert joined["playerId"] not in room["bingo"]["cards"]

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(joined["playerToken"]))).json()
    assert set(room["bingo"]["cards"]) == {joined["playerId"]}


async def test_a_spectator_gets_the_room_but_no_board(client):
    created = await create_room(client)
    key = created["room"]["key"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(created["playerToken"]))

    room = (await client.get(f"{BASE}/rooms/{key}")).json()
    assert room["bingo"]["cards"] == {}
    assert room["bingo"]["turnOrder"]


async def test_a_dealt_board_holds_1_to_25_exactly_once(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    card = room["bingo"]["cards"][created["playerId"]]
    assert sorted(card) == list(range(1, CARD_SIZE + 1))


# --- turns and selection ---------------------------------------------------


async def test_only_the_host_starts_a_round(client):
    created = await create_room(client)
    key = created["room"]["key"]
    joined = await join_room(client, key, "Dev")

    response = await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(joined["playerToken"]))
    assert response.status_code == 403
    assert response.json()["code"] == "not-host"


async def test_a_selection_out_of_turn_is_refused(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    response = await select(client, key, joined["playerToken"], 7)
    assert response.status_code == 409
    assert response.json()["code"] == "not-your-turn"


async def test_a_selection_marks_the_number_and_passes_the_turn(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    room = (await select(client, key, host_token, 17)).json()
    assert room["bingo"]["selected"] == [17]
    assert room["bingo"]["turnOrder"][room["bingo"]["currentTurnIndex"]] == joined["playerId"]

    # The mark is global: the second player sees it on their own board's terms.
    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(joined["playerToken"]))).json()
    assert room["bingo"]["selected"] == [17]


async def test_a_number_already_taken_is_refused(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    await select(client, key, host_token, 17)
    response = await select(client, key, joined["playerToken"], 17)
    assert response.status_code == 409
    assert response.json()["code"] == "number-taken"


@pytest.mark.parametrize("value", [0, 26, -1, "17", 1.5, True, None])
async def test_a_number_outside_the_board_is_refused(client, value):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    response = await select(client, key, token, value)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid-number"


async def test_an_unknown_action_type_is_refused(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    response = await client.post(
        f"{BASE}/rooms/{key}/rounds/current/actions",
        json={"type": "draw_a_cat", "payload": {}},
        headers=auth(token),
    )
    assert response.status_code == 409


async def test_a_retried_selection_is_not_a_second_selection(client):
    """Without an idempotency key a retry looks like a bug to the player."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    headers = {"Idempotency-Key": "retry-1"}
    first = await select(client, key, token, 12, headers)
    second = await select(client, key, token, 12, headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["bingo"]["selected"] == [12]


async def test_two_players_may_send_the_same_idempotency_key(client):
    """The header is whatever a caller chooses to send, so two players using
    the same string must not collide, and neither may read the other's room."""
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    shared = {"Idempotency-Key": "same-string"}
    first = await select(client, key, host_token, 4, shared)
    assert first.status_code == 200, first.text

    second = await select(client, key, joined["playerToken"], 9, shared)
    assert second.status_code == 200, second.text
    assert second.json()["bingo"]["selected"] == [4, 9]
    # The guest got their own board back, not a replay of the host's response.
    assert set(second.json()["bingo"]["cards"]) == {joined["playerId"]}


# --- the turn clock --------------------------------------------------------


async def expire_the_turn(db_session, key: str) -> None:
    """Ages the current turn past its deadline, without waiting 20 seconds."""
    async with db_session() as db:
        room = (await db.execute(sa_select(models.Room).where(models.Room.key == key))).scalar_one()
        game_round = (
            (
                await db.execute(
                    sa_select(models.Round)
                    .where(models.Round.room_id == room.id)
                    .order_by(models.Round.round_number.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        game_round.turn_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()


async def test_a_turn_carries_a_countdown(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]

    lobby = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert lobby["bingo"] is None

    started = (await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))).json()
    remaining = started["bingo"]["turnSecondsRemaining"]
    assert remaining is not None
    assert 0 < remaining <= TURN_SECONDS


async def test_a_turn_that_runs_out_is_played_and_passed_on(client, db_session):
    """The whole point: this has to work when the player is not there.

    Their own client cannot time their turn out, because it is gone. Another
    player's read is what settles it.
    """
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    # The host is on turn and walks away.
    assert await whose_turn(client, key, host_token) == created["playerId"]
    await expire_the_turn(db_session, key)

    # The guest reads the room, and that read is what plays the turn out.
    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(joined["playerToken"]))).json()
    assert len(room["bingo"]["selected"]) == 1
    assert room["bingo"]["turnOrder"][room["bingo"]["currentTurnIndex"]] == joined["playerId"]
    # The number came from the numbers still free, so it is a real move.
    assert 1 <= room["bingo"]["selected"][0] <= CARD_SIZE
    # And the player who inherits the turn gets a full slice of time.
    assert room["bingo"]["turnSecondsRemaining"] > 0


async def test_a_timed_out_turn_is_recorded_as_one(client, db_session):
    """A turn nobody took must not read like a turn somebody took."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    await expire_the_turn(db_session, key)
    await client.get(f"{BASE}/rooms/{key}", headers=auth(token))

    async with db_session() as db:
        rows = (await db.execute(sa_select(models.Event).order_by(models.Event.id))).scalars().all()
        kinds = [row.type for row in rows]
        assert "turn_timed_out" in kinds
        auto = next(row for row in rows if row.type == "number_selected")
        assert auto.payload.get("auto") is True


async def test_only_one_turn_is_played_per_read(client, db_session):
    """A room everybody left must not play itself out to whoever comes back."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    await expire_the_turn(db_session, key)
    first = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert len(first["bingo"]["selected"]) == 1

    # The next turn is freshly clocked, so reading again changes nothing.
    second = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert len(second["bingo"]["selected"]) == 1
    assert joined["playerId"] in second["bingo"]["turnOrder"]


async def test_a_turn_still_running_is_left_alone(client, db_session):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert room["bingo"]["selected"] == []


async def test_a_finished_round_stops_the_clock(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    for value in range(1, CARD_SIZE + 1):
        await select(client, key, token, value)

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert room["phase"] == "round-results"
    assert room["bingo"]["turnSecondsRemaining"] is None


# --- claiming --------------------------------------------------------------


async def test_a_claim_on_an_incomplete_board_is_refused_and_play_continues(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    await select(client, key, token, 1)

    response = await claim(client, key, token)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid-claim"

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert room["phase"] == "playing"
    assert room["bingo"]["winnerId"] is None


async def test_one_line_is_not_a_win(client):
    """Five lines, not one. This is the rule most often got wrong."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    card = room["bingo"]["cards"][created["playerId"]]
    for value in card[0:5]:  # exactly the top row
        await select(client, key, token, value)

    response = await claim(client, key, token)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid-claim"
    assert "1" in response.json()["message"]


async def test_five_lines_wins_and_scores_the_room(client):
    """A win is five lines, and it does not need a full board.

    The stopping point is not fixed in advance on purpose. Lines share cells, so
    the fifth one can arrive on a pick that also completes a fourth — the count
    is what decides, never the number of picks.
    """
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    card = room["bingo"]["cards"][created["playerId"]]

    # Take the first three rows, then fill the remaining cells of the first two
    # columns. That reaches five lines somewhere short of a full board.
    wanted: list[int] = []
    for row in range(3):
        wanted.extend(card[row * 5 : row * 5 + 5])
    for column in range(2):
        wanted.extend(card[row * 5 + column] for row in range(3, 5))
    ordered = list(dict.fromkeys(wanted))

    won = None
    for taken, value in enumerate(ordered, start=1):
        assert (await select(client, key, token, value)).status_code == 200
        response = await claim(client, key, token)
        if response.status_code == 200:
            won = (taken, response.json())
            break
        assert response.json()["code"] == "invalid-claim"

    assert won is not None, "five lines were never reached"
    taken, room = won
    assert taken < CARD_SIZE, "a win must not require the whole board"

    assert room["phase"] == "round-results"
    assert room["bingo"]["winnerId"] == created["playerId"]
    assert len(room["bingo"]["winningLines"]) >= 5
    assert len(room["bingo"]["selected"]) == taken
    assert room["players"][0]["score"] == 100
    assert room["lastRound"][0]["note"] == "Bingo"
    # The reveal: the winner's board is public once the round is over.
    assert created["playerId"] in room["bingo"]["cards"]


async def test_a_non_winner_scores_ten_a_line(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    tokens = [host_token, joined["playerToken"]]
    for index, value in enumerate(range(1, 25)):
        response = await select(client, key, tokens[index % 2], value)
        assert response.status_code == 200, response.text

    # 24 of 25 numbers gone: both boards are one cell short of complete, so
    # each holds the same lines and both players score.
    on_turn = await whose_turn(client, key, host_token)
    winner_token = host_token if on_turn == created["playerId"] else joined["playerToken"]
    response = await claim(client, key, winner_token)
    assert response.status_code == 200, response.text

    room = response.json()
    gains = {row["playerId"]: row["gain"] for row in room["lastRound"]}
    assert 100 in gains.values()
    loser_gain = min(gains.values())
    assert loser_gain > 0 and loser_gain % 10 == 0


async def test_a_second_claim_gets_round_over(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    card = room["bingo"]["cards"][created["playerId"]]
    for value in card:  # solo room: take everything, which ends the round
        response = await select(client, key, token, value)
        if response.status_code != 200:
            break

    response = await claim(client, key, token)
    assert response.status_code == 409
    assert response.json()["code"] == "round-over"


async def test_a_board_that_runs_out_ends_the_round_for_the_closer(client):
    """All 25 gone with nobody claiming. Every board then holds all twelve
    lines, so 'most lines' is a twelve-way tie — the closer takes it."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    for value in range(1, CARD_SIZE + 1):
        response = await select(client, key, token, value)
        assert response.status_code == 200, response.text

    room = response.json()
    assert room["phase"] == "round-results"
    assert room["bingo"]["winnerId"] == created["playerId"]
    assert room["lastRound"][0]["note"] == "Closed the board"
    assert room["lastRound"][0]["gain"] == 100

    # And no further selection is possible.
    assert (await select(client, key, token, 1)).status_code in (409, 422)


# --- phases and host controls ---------------------------------------------


async def test_the_session_finishes_after_the_last_round(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    for value in range(1, CARD_SIZE + 1):
        await select(client, key, token, value)

    response = await client.post(f"{BASE}/rooms/{key}/rounds/advance", headers=auth(token))
    assert response.status_code == 200
    room = response.json()
    assert room["phase"] == "finished"
    assert room["bingo"] is None
    assert room["lastRound"] is None


async def test_advancing_before_the_round_ends_is_the_wrong_phase(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    response = await client.post(f"{BASE}/rooms/{key}/rounds/advance", headers=auth(token))
    assert response.status_code == 409
    assert response.json()["code"] == "wrong-phase"


async def test_a_second_round_deals_fresh_boards_and_keeps_scores(client):
    created = await create_room(client, settings={**DEFAULT_SETTINGS, "rounds": 2})
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    for value in range(1, CARD_SIZE + 1):
        await select(client, key, token, value)

    response = await client.post(f"{BASE}/rooms/{key}/rounds/advance", headers=auth(token))
    room = response.json()
    assert room["phase"] == "playing"
    assert room["round"] == 2
    assert room["bingo"]["selected"] == []
    assert room["players"][0]["score"] == 100


async def test_replay_returns_to_the_lobby_and_zeroes_the_scores(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    for value in range(1, CARD_SIZE + 1):
        await select(client, key, token, value)
    await client.post(f"{BASE}/rooms/{key}/rounds/advance", headers=auth(token))

    response = await client.post(f"{BASE}/rooms/{key}/replay", headers=auth(token))
    assert response.status_code == 200
    room = response.json()
    assert room["phase"] == "lobby"
    assert room["round"] == 1
    assert room["bingo"] is None
    assert room["players"][0]["score"] == 0

    # And a replayed session can start a round without colliding with the old one.
    assert (await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))).status_code == 200


async def test_replay_is_allowed_straight_from_the_results_screen(client):
    """The results screen of a final round is where a session actually ends.

    The client offers "Play again, same room" there as well as on the
    scoreboard, so replay has to be accepted in `round-results` and not only in
    `finished`. A phase guard added to `replay_session` would break that button
    with nothing else failing.
    """
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    for value in range(1, CARD_SIZE + 1):
        await select(client, key, token, value)

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(token))).json()
    assert room["phase"] == "round-results"
    assert room["players"][0]["score"] > 0

    response = await client.post(f"{BASE}/rooms/{key}/replay", headers=auth(token))
    assert response.status_code == 200, response.text
    room = response.json()
    assert room["phase"] == "lobby"
    assert room["round"] == 1
    assert room["bingo"] is None
    assert all(player["score"] == 0 for player in room["players"])

    # And the room is genuinely usable again, not just relabelled.
    started = await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    assert started.status_code == 200, started.text
    assert started.json()["phase"] == "playing"


async def test_the_host_ends_the_session(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    response = await client.post(f"{BASE}/rooms/{key}/end", headers=auth(token))
    assert response.json()["phase"] == "finished"


async def test_lock_closes_the_room_to_new_players(client):
    created = await create_room(client, settings={**DEFAULT_SETTINGS, "privacy": "Key only"})
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/lock", headers=auth(token))
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    response = await client.post(
        f"{BASE}/rooms/{key}/players", json={"name": "Late", "color": "mint"}
    )
    assert response.json()["code"] == "room-locked"


# --- removal ---------------------------------------------------------------


async def test_the_host_removes_a_player_without_skipping_a_turn(client):
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    second = await join_room(client, key, "Dev")
    third = await join_room(client, key, "Ada", color="yellow")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    # Host takes a number, so it is now the second player's turn.
    await select(client, key, host_token, 3)
    assert await whose_turn(client, key, host_token) == second["playerId"]

    # Removing the third player must not move the turn.
    response = await client.delete(
        f"{BASE}/rooms/{key}/players/{third['playerId']}", headers=auth(host_token)
    )
    assert response.status_code == 200
    assert await whose_turn(client, key, host_token) == second["playerId"]

    # Removing the player who IS on turn moves play to the next one along.
    await client.delete(
        f"{BASE}/rooms/{key}/players/{second['playerId']}", headers=auth(host_token)
    )
    assert await whose_turn(client, key, host_token) == created["playerId"]


async def test_the_host_cannot_be_removed(client):
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    response = await client.delete(
        f"{BASE}/rooms/{key}/players/{created['playerId']}", headers=auth(token)
    )
    assert response.status_code == 403
    assert response.json()["code"] == "not-host"


async def test_a_player_cannot_remove_another_player(client):
    created = await create_room(client)
    key = created["room"]["key"]
    second = await join_room(client, key, "Dev")
    third = await join_room(client, key, "Ada", color="yellow")

    response = await client.delete(
        f"{BASE}/rooms/{key}/players/{third['playerId']}",
        headers=auth(second["playerToken"]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "not-host"


# --- mid-round join --------------------------------------------------------


async def test_a_mid_round_joiner_is_dealt_in_without_moving_the_turn(client):
    created = await create_room(client, settings={**DEFAULT_SETTINGS, "privacy": "Key only"})
    key, host_token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))
    await select(client, key, host_token, 5)

    joined = await join_room(client, key, "Late")
    room = joined["room"]
    assert joined["playerId"] in room["bingo"]["cards"]
    assert room["bingo"]["turnOrder"][-1] == joined["playerId"]
    # A solo host wraps back to themselves; the running turn is untouched.
    assert await whose_turn(client, key, host_token) == created["playerId"]


# --- host promotion --------------------------------------------------------


async def test_a_room_is_not_stranded_when_the_host_goes(client, db_session):
    """Only the host can start a round, so a lobby whose host closed the tab
    would never begin. The longest-present player takes over."""
    created = await create_room(client)
    key = created["room"]["key"]
    second = await join_room(client, key, "Dev")

    async with db_session() as db:
        host = (
            await db.execute(
                sa_select(models.Player).where(models.Player.id == UUID(created["playerId"]))
            )
        ).scalar_one()
        host.last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()

    room = (await client.get(f"{BASE}/rooms/{key}", headers=auth(second["playerToken"]))).json()
    assert room["hostId"] == second["playerId"]

    # And the new host can actually run the room.
    response = await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(second["playerToken"]))
    assert response.status_code == 200


# --- rate limits -----------------------------------------------------------


async def test_actions_are_rate_limited(client):
    """There is no login, so nothing else stops a script."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))

    statuses = []
    for value in range(1, 12):
        response = await client.post(
            f"{BASE}/rooms/{key}/rounds/current/actions",
            json={"type": "select_number", "payload": {"value": value}},
            headers=auth(token),
        )
        statuses.append(response.status_code)

    assert 429 in statuses
    limited = next(status for status in statuses if status == 429)
    assert limited == 429


async def test_room_creation_is_rate_limited(client):
    statuses = []
    for index in range(12):
        response = await client.post(
            f"{BASE}/rooms",
            json={
                "gameId": "bingo",
                "settings": DEFAULT_SETTINGS,
                "hostName": f"Host{index}",
                "hostColor": "peach",
            },
        )
        statuses.append(response.status_code)
    assert 429 in statuses


# --- caching ---------------------------------------------------------------


async def test_an_unchanged_room_can_be_polled_cheaply(client):
    """Everyone polls every two seconds whether or not anything changed."""
    created = await create_room(client)
    key, token = created["room"]["key"], created["playerToken"]

    first = await client.get(f"{BASE}/rooms/{key}", headers=auth(token))
    etag = first.headers["ETag"]
    assert etag

    unchanged = await client.get(
        f"{BASE}/rooms/{key}", headers={**auth(token), "If-None-Match": etag}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["ETag"] == etag

    # A change invalidates it, and the next poll gets a body again.
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(token))
    changed = await client.get(
        f"{BASE}/rooms/{key}", headers={**auth(token), "If-None-Match": etag}
    )
    assert changed.status_code == 200
    assert changed.headers["ETag"] != etag


async def test_a_validator_is_not_shared_between_viewers(client):
    """Two viewers of one room version hold different boards, so one of them
    must not be told 'unchanged' on the strength of the other's tag."""
    created = await create_room(client)
    key, host_token = created["room"]["key"], created["playerToken"]
    joined = await join_room(client, key, "Dev")
    await client.post(f"{BASE}/rooms/{key}/rounds", headers=auth(host_token))

    host_tag = (await client.get(f"{BASE}/rooms/{key}", headers=auth(host_token))).headers["ETag"]
    guest = await client.get(
        f"{BASE}/rooms/{key}", headers={**auth(joined["playerToken"]), "If-None-Match": host_tag}
    )
    assert guest.status_code == 200
    assert set(guest.json()["bingo"]["cards"]) == {joined["playerId"]}


# --- pure rules ------------------------------------------------------------


def test_a_full_board_holds_all_twelve_lines():
    card = list(range(1, CARD_SIZE + 1))
    assert len(find_winning_lines(card, card)) == 12


def test_lines_can_share_a_cell():
    """One number can complete two lines at once, so count lines, not picks."""
    card = list(range(1, CARD_SIZE + 1))
    # The whole top row and the whole first column but for their shared corner.
    selected = [1, 2, 3, 4, 5, 6, 11, 16, 21]
    lines = find_winning_lines(card, selected)
    kinds = {(line.kind, line.index) for line in lines}
    assert ("row", 1) in kinds
    assert ("column", 1) in kinds


def test_the_catalogue_matches_the_seeded_rows():
    """The migration carries a literal copy of the catalogue, deliberately.

    This is the check that tells you the two drifted, which is the only cost of
    keeping a migration free of live application imports.
    """
    from pathlib import Path

    migration = Path("alembic/versions/a1f3c7d90b21_add_playroom_tables.py").read_text()
    for row in CATALOGUE:
        assert f'"id": "{row["id"]}"' in migration
        assert f'"status": "{row["status"]}"' in migration
