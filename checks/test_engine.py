"""Numeric, point-in-time and market-calendar regression checks (standard library)."""
import json
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import calendar_cn as cn
import engine


class Calculation(unittest.TestCase):
    def test_known_step_and_warmup(self):
        ds = [date(2025, 1, 1) + timedelta(days=i) for i in range(80)]
        prices = dict.fromkeys(ds, 100.)
        prices[ds[-1]] = 110.
        bars = engine.indicators(ds, prices, 80)
        self.assertIsNone(bars[24][1])
        self.assertIsNone(bars[50][4])
        self.assertEqual(bars[51][4], [0]*9)
        d = 10 * (2/13 - 2/27)
        for actual, wanted in zip(bars[-1][1:4], [d, d/5, 2*d*4/5]):
            self.assertAlmostEqual(actual, wanted, places=5)
        self.assertEqual(bars[-1][4], [30, 25, 25, 20, 0, 0, 0, 0, 0])
        prices[ds[-1]] = 90.
        self.assertEqual(engine.indicators(ds, prices, 1)[0][4], [0, 0, 0, 0, 25, 20, 25, 15, 15])

    def test_missing_close_is_not_carried_forward(self):
        ds = [date(2025, 1, 1) + timedelta(days=i) for i in range(61)]
        prices = dict.fromkeys(ds, 100.)
        del prices[ds[-2]]
        self.assertEqual(engine.indicators(ds, prices, 2), [None, [100., None, None, None, None]])

    def test_holiday_and_partial_periods(self):
        pre_close = cn.shanghai_now(datetime(2026, 11, 4, 14, 59))
        self.assertEqual(cn.latest_closed_session(pre_close), date(2026, 11, 3))
        self.assertEqual(cn.period_end(date(2026, 9, 28), 'week'), date(2026, 9, 30))
        for p in ('W', 'M'):
            self.assertEqual(engine.expected_end(p, date(2026, 11, 3), None), date(2026, 10, 30))
            _, closed = engine.market_grid([date(2026, 10, 30), date(2026, 11, 3)], p, date(2026, 11, 3))
            self.assertEqual(closed, [True, False])

    def test_real_history_replay_uses_only_closes_known_then(self):
        from source_ths import load_cache
        catalog, history = load_cache()
        sample = [r for r in catalog if r['code'] == '881121']
        self.assertEqual(len(sample), 1)
        cut = engine.make_snapshot(sample, history, cn.shanghai_now(datetime(2026, 9, 30, 17)))
        later = engine.make_snapshot(sample, history, cn.shanghai_now(datetime(2026, 11, 4, 12)))
        for p in engine.KEEP:
            j = later['axes'][p]['dates'].index('2026-09-30')
            self.assertTrue(cut['axes'][p]['closed'][-1])
            self.assertEqual(cut['rows'][0]['series'][p][-1], later['rows'][0]['series'][p][j])
        # A market-wide missing completed close remains a blank last slot, with an older actual date.
        stale = {sample[0]['code']: [b for b in history[sample[0]['code']] if b[0] < '2026-09-30']}
        view = engine.make_snapshot(sample, stale, cn.shanghai_now(datetime(2026, 9, 30, 17)))
        self.assertEqual((view['asOf'],view['fresh']), ('2026-09-29',0))
        for p in engine.KEEP:
            self.assertIsNone(view['rows'][0]['series'][p][-1])

    def test_published_snapshot(self):
        from source_ths import load_cache
        catalog, history = load_cache()
        payload = (ROOT/'dist/market.js').read_text(encoding='utf-8')
        site = json.loads(payload.removeprefix('window.SECTOR_SNAPSHOT=').strip().removesuffix(';'))
        self.assertEqual([(x['name'],x['code']) for x in site['rows']], [(x['name'],x['code']) for x in catalog])
        self.assertEqual(len({r['code'] for r in catalog}), len(catalog))
        for row in site['rows']:
            for p, grid in site['axes'].items():
                bars = row['series'][p]
                self.assertEqual(len(bars),len(grid['dates']))
                self.assertTrue(all(not b or b[4] is None for b, closed in zip(bars,grid['closed']) if not closed))
                i = max(i for i,f in enumerate(grid['closed']) if f)
                b = bars[i]
                if not b:
                    continue  # A genuine missing period must not be given a score.
                self.assertEqual(b[0], dict(history[row['code']])[grid['dates'][i]])
                if b[1] is not None:
                    self.assertAlmostEqual(b[3],2*(b[1]-b[2]),delta=.00003)
                if b[4]:
                    self.assertTrue(all(0<=x<=cap for x,cap in zip(b[4],[30,25,25,20,25,20,25,15,15])))

if __name__ == '__main__':
    unittest.main(verbosity=2)
