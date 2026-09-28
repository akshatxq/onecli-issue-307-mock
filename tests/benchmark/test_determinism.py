import unittest
from scripts.benchmark import run_local
class DeterminismTests(unittest.TestCase):
    def test_five_runs(self):
        report = run_local(5)
        self.assertTrue(report['reference_conforms'])
        for mode in ('buggy', 'fixed'):
            runs = report['modes'][mode]
            self.assertEqual(len(runs), 5)
            self.assertTrue(all(run == runs[0] for run in runs))
            self.assertEqual(runs[0]['verdict'], 'FAIL' if mode == 'buggy' else 'PASS')
        self.assertEqual(report['modes']['buggy'][0]['reply'], report['modes']['fixed'][0]['reply'])
