import io
import random
import unittest
from types import SimpleNamespace

from cogs.blind_vaults import (
    AuctionRound, VaultPlayer, VAULTS, BID_STEPS, validate_bid,
    resolve_auction_round, render_vault_board,
)


def player(uid):
    return VaultPlayer(SimpleNamespace(id=uid, name="Player {}".format(uid), display_name="Player {}".format(uid)))


class BlindVaultRulesTests(unittest.TestCase):
    def test_round_has_one_of_each_vault_type_and_probe_hint(self):
        players=[player(1),player(2),player(3)]
        state=AuctionRound.create(1,players,random.Random(42))
        self.assertEqual(set(state.contents.values()),{"legendary","normal","trap"})
        self.assertIn(state.probe_user_id,{1,2,3})
        self.assertTrue(state.probe_hint)

    def test_bid_validation_and_cash_limit(self):
        p=player(1)
        validate_bid(p,100,0)
        with self.assertRaises(ValueError):validate_bid(p,101,0)
        p.cash=200
        with self.assertRaises(ValueError):validate_bid(p,250,0)

    def test_legendary_vault_pays_three_or_four_times_bid(self):
        p=player(1)
        state=AuctionRound(1,{1:"legendary",2:"normal",3:"trap"},4,1,"hint",current_bid=500,leader_id=1)
        result=resolve_auction_round([p],state,1)
        self.assertEqual(result["payout"],2000)
        self.assertEqual(p.cash,6500)
        self.assertEqual(p.points,15)

    def test_normal_vault_returns_twenty_percent_profit(self):
        p=player(1)
        state=AuctionRound(1,{1:"normal",2:"legendary",3:"trap"},3,1,"hint",current_bid=500,leader_id=1)
        result=resolve_auction_round([p],state,1)
        self.assertEqual(result["payout"],600)
        self.assertEqual(p.cash,5100)
        self.assertEqual(p.points,11)

    def test_trap_loses_full_bid_and_three_points(self):
        p=player(1)
        state=AuctionRound(1,{1:"trap",2:"legendary",3:"normal"},3,1,"hint",current_bid=500,leader_id=1)
        result=resolve_auction_round([p],state,1)
        self.assertEqual(result["payout"],0)
        self.assertEqual(p.cash,4500)
        self.assertEqual(p.points,7)

    def test_round_cannot_resolve_twice_or_use_invalid_vault(self):
        p=player(1)
        state=AuctionRound(1,{1:"trap",2:"legendary",3:"normal"},3,1,"hint",current_bid=100,leader_id=1)
        with self.assertRaisesRegex(ValueError,"invalid_vault"):resolve_auction_round([p],state,4)
        resolve_auction_round([p],state,1)
        with self.assertRaisesRegex(ValueError,"round_already_resolved"):resolve_auction_round([p],state,1)


class BlindVaultRenderingTests(unittest.TestCase):
    def test_board_is_valid_png(self):
        players=[player(1),player(2),player(3)]
        image=render_vault_board(players,round_number=2,current_bid=750,leader_id=2,seconds_left=4.5)
        self.assertEqual(image.size,(1200,790))
        self.assertEqual(image.mode,"RGBA")
        buffer=io.BytesIO();image.save(buffer,format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(buffer.getvalue()),1000)

    def test_bid_steps_are_exact(self):
        self.assertEqual(BID_STEPS,(100,250,500))
        self.assertEqual(VAULTS,(1,2,3))


if __name__=="__main__":unittest.main()
