"""Rootless mode: spec fields, argv builders, nft rules and process state; no namespace is created."""
import unittest

from worker_sandbox import contracts as c


class RuntimeSpecTests(unittest.TestCase):
    def test_root_mode_is_the_default_and_needs_no_bases(self):
        spec = c.RuntimeSpec()
        self.assertEqual((spec.mode, spec.subuid_base, spec.subgid_base), ('root', None, None))
        self.assertEqual(c.loads(b'{"account":"worker-sandbox","control_root":"/c","python":"/usr/bin/python3",'
                                 b'"worker_root":"/w"}', c.RuntimeSpec).mode, 'root')

    def test_rootless_mode_requires_both_bases(self):
        for fields in ({}, {'subuid_base': 100000}, {'subgid_base': 100000}):
            with self.subTest(fields=fields), self.assertRaisesRegex(c.ContractError, 'subordinate'):
                c.validate({'mode': 'rootless', **fields}, c.RuntimeSpec)
        spec = c.validate({'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000}, c.RuntimeSpec)
        self.assertEqual((spec.subuid_base, spec.subgid_base), (100000, 100000))

    def test_rootless_roots_stay_absolute_and_bases_nonnegative(self):
        base = {'mode': 'rootless', 'subuid_base': 100000, 'subgid_base': 100000}
        for fields in ({'worker_root': 'relative'}, {'control_root': 'relative'}, {'subuid_base': -1}, {'mode': 'other'}):
            with self.subTest(fields=fields), self.assertRaises(c.ContractError):
                c.validate({**base, **fields}, c.RuntimeSpec)


if __name__ == '__main__':
    unittest.main()
