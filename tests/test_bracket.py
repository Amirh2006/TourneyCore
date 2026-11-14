"""Tests for the double-elimination bracket engine."""

from __future__ import annotations

import unittest

from core.bracket import (
    advance_match,
    build_bracket,
    find_next_match,
    is_match_dead,
    is_tournament_over,
    make_grand_final,
)
from core.models import (
    BracketSide,
    MatchStatus,
    Player,
    PlayerStatus,
    WinMethod,
    generate_entry_code,
)


def make_player(system_id: str, name: str) -> Player:
    return Player(system_id=system_id, name=name, entry_code=generate_entry_code())


class BuildBracketTests(unittest.TestCase):
    def test_four_player_bracket_shape(self) -> None:
        players = [make_player("sys_1", name) for name in "ABCD"]
        matches = build_bracket("sys_1", [p.id for p in players])

        winners = [m for m in matches if m.bracket is BracketSide.WINNERS]
        losers = [m for m in matches if m.bracket is BracketSide.LOSERS]
        finals = [m for m in matches if m.bracket is BracketSide.GRAND_FINAL]

        self.assertEqual(len(winners), 3)  # two semi-finals + final
        self.assertEqual(len(losers), 2)  # minor + major final
        self.assertEqual(len(finals), 2)  # first final + reset

        first_round = [m for m in winners if m.round == 1]
        self.assertTrue(all(m.status is MatchStatus.READY for m in first_round))
        self.assertTrue(all(m.has_both_players for m in first_round))

    def test_all_matches_are_linked(self) -> None:
        players = [make_player("sys_1", name) for name in "ABCD"]
        matches = build_bracket("sys_1", [p.id for p in players])
        by_id = {m.id: m for m in matches}

        for match in matches:
            if match.next_winner_match_id is not None:
                self.assertIn(match.next_winner_match_id, by_id)
            if match.next_loser_match_id is not None:
                self.assertIn(match.next_loser_match_id, by_id)

        # The only match without a forward winner link is the reset final.
        terminal = [m for m in matches if m.next_winner_match_id is None]
        self.assertEqual(len(terminal), 1)
        self.assertIs(terminal[0].bracket, BracketSide.GRAND_FINAL)
        self.assertEqual(terminal[0].round, 2)

    def test_rejects_duplicate_players(self) -> None:
        player = make_player("sys_1", "A")
        with self.assertRaises(ValueError):
            build_bracket("sys_1", [player.id, player.id])

    def test_three_player_bracket_has_a_bye(self) -> None:
        players = [make_player("sys_1", name) for name in "ABC"]
        matches = build_bracket("sys_1", [p.id for p in players])

        byes = [m for m in matches if m.is_bye]
        self.assertEqual(len(byes), 1)
        self.assertIs(byes[0].status, MatchStatus.COMPLETED)

    def test_padded_bracket_marks_unreachable_matches(self) -> None:
        players = [make_player("sys_1", f"P{index}") for index in range(1, 6)]
        matches = build_bracket("sys_1", [p.id for p in players])

        dead = [match for match in matches if is_match_dead(matches, match)]
        self.assertTrue(dead)
        self.assertTrue(
            all(match.status is MatchStatus.PENDING for match in dead)
        )

        full = build_bracket(
            "sys_1", [make_player("sys_1", name).id for name in "ABCD"]
        )
        self.assertFalse(
            [match for match in full if is_match_dead(full, match)]
        )

    def test_every_roster_size_finishes(self) -> None:
        for size in range(2, 13):
            with self.subTest(size=size):
                players = [
                    make_player("sys_1", f"P{index}") for index in range(1, size + 1)
                ]
                matches = build_bracket("sys_1", [p.id for p in players])

                guard = 0
                while not is_tournament_over(matches, players):
                    guard += 1
                    self.assertLess(guard, 200, "tournament did not progress")
                    ready = [
                        match
                        for match in matches
                        if match.status is MatchStatus.READY
                    ]
                    self.assertTrue(ready, "tournament stalled with no ready match")
                    match = ready[0]
                    advance_match(
                        matches, match.id, match.player1_id, players=players
                    )

                active = [
                    player
                    for player in players
                    if player.status
                    not in (PlayerStatus.ELIMINATED, PlayerStatus.WITHDRAWN)
                ]
                self.assertEqual(len(active), 1)
                champion = active[0]
                self.assertIn(champion.losses, (0, 1))
                for player in players:
                    if player is not champion:
                        self.assertEqual(player.losses, 2)


class AdvanceMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.players = [make_player("sys_1", name) for name in "ABCD"]
        self.matches = build_bracket("sys_1", [p.id for p in self.players])
        self.by_id = {m.id: m for m in self.matches}

    def play(self, match, winner, **kwargs):
        return advance_match(
            self.matches,
            match.id,
            winner.id,
            players=self.players,
            **kwargs,
        )

    def test_winner_and_loser_are_forwarded(self) -> None:
        first = self.by_id[
            next(
                m.id
                for m in self.matches
                if m.bracket is BracketSide.WINNERS
                and m.round == 1
                and m.position == 0
            )
        ]
        winner = self.players[0] if first.player1_id == self.players[0].id else self.players[1]
        loser = next(
            p
            for p in self.players
            if p.id in (first.player1_id, first.player2_id) and p.id != winner.id
        )

        next_winner, next_loser = self.play(first, winner)

        self.assertIsNotNone(next_winner)
        self.assertIsNotNone(next_loser)
        self.assertIs(next_winner.bracket, BracketSide.WINNERS)
        self.assertIs(next_loser.bracket, BracketSide.LOSERS)
        self.assertIn(winner.id, (next_winner.player1_id, next_winner.player2_id))
        self.assertIn(loser.id, (next_loser.player1_id, next_loser.player2_id))
        self.assertEqual(winner.wins, 1)
        self.assertEqual(loser.losses, 1)

    def test_completing_twice_is_rejected(self) -> None:
        first = next(
            m
            for m in self.matches
            if m.bracket is BracketSide.WINNERS and m.round == 1 and m.position == 0
        )
        winner_id = first.player1_id
        winner = next(p for p in self.players if p.id == winner_id)
        self.play(first, winner)
        with self.assertRaises(ValueError):
            self.play(first, winner)

    def test_winner_must_be_a_participant(self) -> None:
        first = next(
            m
            for m in self.matches
            if m.bracket is BracketSide.WINNERS and m.round == 1 and m.position == 0
        )
        outsider = next(
            p
            for p in self.players
            if p.id not in (first.player1_id, first.player2_id)
        )
        with self.assertRaises(ValueError):
            self.play(first, outsider)

    def test_full_tournament_needs_reset_final(self) -> None:
        """Play a complete 4-player event where the losers champion forces a reset."""

        def play_any(match, winner):
            return self.play(match, winner)

        # Winners round 1
        wb_r1 = sorted(
            (m for m in self.matches if m.bracket is BracketSide.WINNERS and m.round == 1),
            key=lambda m: m.position,
        )
        a, c = wb_r1[0].player1_id, wb_r1[0].player2_id
        d, b = wb_r1[1].player1_id, wb_r1[1].player2_id
        by_id = {p.id: p for p in self.players}

        play_any(wb_r1[0], by_id[a])
        play_any(wb_r1[1], by_id[d])

        wb_final = next(
            m for m in self.matches if m.bracket is BracketSide.WINNERS and m.round == 2
        )
        self.assertEqual({wb_final.player1_id, wb_final.player2_id}, {a, d})

        lb_minor = next(
            m for m in self.matches if m.bracket is BracketSide.LOSERS and m.round == 1
        )
        self.assertEqual({lb_minor.player1_id, lb_minor.player2_id}, {c, b})

        # Losers champion (d) wins the winners final, then loses the first final
        play_any(wb_final, by_id[d])

        play_any(lb_minor, by_id[c])

        lb_major = next(
            m for m in self.matches if m.bracket is BracketSide.LOSERS and m.round == 2
        )
        self.assertEqual({lb_major.player1_id, lb_major.player2_id}, {c, a})

        play_any(lb_major, by_id[a])

        gf1 = next(
            m for m in self.matches if m.bracket is BracketSide.GRAND_FINAL and m.round == 1
        )
        self.assertEqual({gf1.player1_id, gf1.player2_id}, {d, a})

        # Losers finalist (a) wins the first final -> reset match required
        reset, _ = play_any(gf1, by_id[a])
        self.assertIsNotNone(reset)
        self.assertIs(reset.bracket, BracketSide.GRAND_FINAL)
        self.assertEqual(reset.round, 2)
        self.assertEqual(reset.status, MatchStatus.READY)
        self.assertEqual({reset.player1_id, reset.player2_id}, {d, a})

        # Winners champion (d) takes the reset and the title
        play_any(reset, by_id[d])

        self.assertTrue(is_tournament_over(self.matches, self.players))
        self.assertEqual(by_id[d].losses, 1)
        self.assertIs(by_id[a].status, PlayerStatus.ELIMINATED)
        self.assertEqual(by_id[a].losses, 2)

    def test_winners_champion_can_win_outright(self) -> None:
        players = self.players
        by_id = {p.id: p for p in players}

        wb_r1 = sorted(
            (m for m in self.matches if m.bracket is BracketSide.WINNERS and m.round == 1),
            key=lambda m: m.position,
        )
        first = wb_r1[0]
        winner = by_id[first.player1_id]
        self.play(first, winner)
        second = wb_r1[1]
        second_winner = by_id[second.player1_id]
        self.play(second, second_winner)

        wb_final = next(
            m for m in self.matches if m.bracket is BracketSide.WINNERS and m.round == 2
        )
        self.play(wb_final, winner)

        lb_minor = next(
            m for m in self.matches if m.bracket is BracketSide.LOSERS and m.round == 1
        )
        lb_winner = by_id[lb_minor.player1_id]
        self.play(lb_minor, lb_winner)

        lb_major = next(
            m for m in self.matches if m.bracket is BracketSide.LOSERS and m.round == 2
        )
        self.play(lb_major, lb_winner)

        gf1 = next(
            m for m in self.matches if m.bracket is BracketSide.GRAND_FINAL and m.round == 1
        )
        # Winners champion wins outright; no reset match is started.
        result_winner, result_loser = self.play(gf1, winner)
        self.assertIsNone(result_winner)
        self.assertIsNone(result_loser)

        reset = next(
            m for m in self.matches if m.bracket is BracketSide.GRAND_FINAL and m.round == 2
        )
        self.assertIs(reset.status, MatchStatus.PENDING)
        self.assertTrue(is_tournament_over(self.matches, self.players))


class FindNextMatchTests(unittest.TestCase):
    def test_finds_scheduled_match(self) -> None:
        players = [make_player("sys_1", name) for name in "ABCD"]
        matches = build_bracket("sys_1", [p.id for p in players])
        target = players[0]

        found = find_next_match(matches, target.id)
        self.assertIsNotNone(found)
        self.assertIn(target.id, (found.player1_id, found.player2_id))

    def test_returns_none_for_unknown_player(self) -> None:
        players = [make_player("sys_1", name) for name in "ABCD"]
        matches = build_bracket("sys_1", [p.id for p in players])
        self.assertIsNone(find_next_match(matches, "plr_missing"))

    def test_side_filter(self) -> None:
        players = [make_player("sys_1", name) for name in "ABCD"]
        matches = build_bracket("sys_1", [p.id for p in players])
        target = players[0]
        self.assertIsNone(find_next_match(matches, target.id, side=BracketSide.LOSERS))


class GrandFinalHelperTests(unittest.TestCase):
    def test_make_grand_final_creates_slots(self) -> None:
        first, reset = make_grand_final([], "sys_1", "plr_wb", "plr_lb")
        self.assertEqual(first.bracket, BracketSide.GRAND_FINAL)
        self.assertEqual(first.round, 1)
        self.assertEqual({first.player1_id, first.player2_id}, {"plr_wb", "plr_lb"})
        self.assertIsNotNone(reset)
        self.assertEqual(reset.round, 2)

    def test_make_grand_final_without_losers_player(self) -> None:
        first, reset = make_grand_final([], "sys_1", "plr_wb", None)
        self.assertTrue(first.is_bye)
        self.assertIsNone(reset)


class TwoPlayerTournamentTests(unittest.TestCase):
    def test_two_player_event_can_require_reset(self) -> None:
        players = [make_player("sys_1", "A"), make_player("sys_1", "B")]
        matches = build_bracket("sys_1", [p.id for p in players])
        opening = next(m for m in matches if m.bracket is BracketSide.WINNERS)

        winner, loser = players[0], players[1]
        if opening.player1_id != winner.id:
            winner, loser = loser, winner

        advance_match(matches, opening.id, winner.id, players=players)

        gf1 = next(m for m in matches if m.bracket is BracketSide.GRAND_FINAL and m.round == 1)
        self.assertEqual({gf1.player1_id, gf1.player2_id}, {players[0].id, players[1].id})

        # The opening-match loser wins the first final and forces a reset.
        reset, _ = advance_match(matches, gf1.id, loser.id, players=players)
        self.assertIsNotNone(reset)
        self.assertEqual(reset.round, 2)

        advance_match(matches, reset.id, winner.id, players=players)
        self.assertTrue(is_tournament_over(matches, players))
        self.assertEqual(winner.losses, 1)
        self.assertEqual(loser.losses, 2)


class TournamentOverTests(unittest.TestCase):
    def test_single_active_player_finishes_event(self) -> None:
        players = [make_player("sys_1", name) for name in "AB"]
        self.assertFalse(is_tournament_over([], players))
        players[1].register_loss()
        self.assertFalse(is_tournament_over([], players))
        players[1].register_loss()
        self.assertTrue(is_tournament_over([], players))

    def test_withdrawn_players_do_not_count(self) -> None:
        players = [make_player("sys_1", name) for name in "AB"]
        players[1].withdraw()
        self.assertTrue(is_tournament_over([], players))


if __name__ == "__main__":
    unittest.main()
