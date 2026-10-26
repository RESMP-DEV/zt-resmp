#!/usr/bin/env python3
import unittest

import public_data_check


class PublicDataCheckTests(unittest.TestCase):
    def test_forbidden_patterns_are_specific(self) -> None:
        network = public_data_check.FORBIDDEN["literal network identifier"]
        self.assertTrue(network.search('network_id: "0123456789abcdef"'))
        self.assertFalse(network.search('network_id: "<network-id>"'))


if __name__ == "__main__":
    unittest.main()
