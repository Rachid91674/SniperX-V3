import unittest
import datetime
import dateparser # Required by SniperX V2.py functions
from unittest.mock import patch, mock_open
import importlib.util
import unicodedata # Was implicitly imported by the re-defined sanitize_name

# Load SniperX V2.py as a module
# The module name 'sniperx_v2_module' will be used to access its functions and variables.
# 'SniperX V2.py' is the path to the file.
try:
    spec = importlib.util.spec_from_file_location("sniperx_v2_module", "SniperX V2.py")
    sniperx_v2_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sniperx_v2_module)
except FileNotFoundError:
    print("Error: SniperX V2.py not found. Make sure it's in the same directory.")
    # Handle the error appropriately, perhaps by skipping tests or exiting
    sniperx_v2_module = None # Ensure it's defined for later checks

# --- End of functions to be imported/mocked from SniperX V2.py ---

class TestSniperXV2(unittest.TestCase):

    def setUp(self):
        # No longer need to reset global_test_config as it's removed.
        # Patches will be applied directly in test methods where needed.
        pass

    @unittest.skipIf(sniperx_v2_module is None, "SniperX V2.py module not loaded")
    def test_filter_preliminary(self):
        now = datetime.datetime.now(datetime.timezone.utc)

        test_config_values = {
            "PRELIM_LIQUIDITY_THRESHOLD": 5000.0,
            "PRELIM_MIN_PRICE_USD": 0.00001,
            "PRELIM_MAX_PRICE_USD": 0.0004,
            "PRELIM_AGE_DELTA_MINUTES": 120.0,
        }

        tokens = [
            # Should pass
            {"liquidity": {"usd": 6000}, "priceUsd": 0.0001, "graduatedAt": (now - datetime.timedelta(minutes=10)).isoformat(), "tokenAddress": "pass1"},
            # Fail: liquidity too low
            {"liquidity": {"usd": 4000}, "priceUsd": 0.0001, "graduatedAt": (now - datetime.timedelta(minutes=10)).isoformat(), "tokenAddress": "fail_liq"},
            # Fail: price too low
            {"liquidity": {"usd": 6000}, "priceUsd": 0.000001, "graduatedAt": (now - datetime.timedelta(minutes=10)).isoformat(), "tokenAddress": "fail_price_low"},
            # Fail: price too high
            {"liquidity": {"usd": 6000}, "priceUsd": 0.0005, "graduatedAt": (now - datetime.timedelta(minutes=10)).isoformat(), "tokenAddress": "fail_price_high"},
            # Fail: too old
            {"liquidity": {"usd": 6000}, "priceUsd": 0.0001, "graduatedAt": (now - datetime.timedelta(minutes=130)).isoformat(), "tokenAddress": "fail_age"},
            # Pass: liquidity as float
            {"liquidity": 7000.0, "priceUsd": "0.0002", "graduatedAt": (now - datetime.timedelta(minutes=30)).isoformat(), "tokenAddress": "pass2"},
            # Pass: Edge case liquidity - uses values from test_config_values, made 1 minute younger to avoid exact boundary issues
            {"liquidity": {"usd": test_config_values["PRELIM_LIQUIDITY_THRESHOLD"]}, "priceUsd": test_config_values["PRELIM_MIN_PRICE_USD"], "graduatedAt": (now - datetime.timedelta(minutes=int(test_config_values["PRELIM_AGE_DELTA_MINUTES"] - 1))).isoformat(), "tokenAddress": "pass_edge"},
        ]

        # Patch the global variables in the imported sniperx_v2_module
        with patch.dict(sniperx_v2_module.__dict__, test_config_values):
            filtered = sniperx_v2_module.filter_preliminary(tokens)

        filtered_addrs = [t["tokenAddress"] for t in filtered]

        self.assertIn("pass1", filtered_addrs)
        self.assertIn("pass2", filtered_addrs)
        self.assertIn("pass_edge", filtered_addrs)
        self.assertEqual(len(filtered_addrs), 3)

    @unittest.skipIf(sniperx_v2_module is None, "SniperX V2.py module not loaded")
    def test_sanitize_name(self):
        self.assertEqual(sniperx_v2_module.sanitize_name("Test Token Alpha"), "Test Token Alpha")
        # Use chr(0) to represent the null byte safely in a string literal
        self.assertEqual(sniperx_v2_module.sanitize_name(f"Token with /{chr(0)}control chars"), "Token with /control chars")
        self.assertEqual(sniperx_v2_module.sanitize_name("  LeadingSpaces"), "LeadingSpaces")
        self.assertEqual(sniperx_v2_module.sanitize_name("TrailingSpaces  "), "TrailingSpaces")
        self.assertEqual(sniperx_v2_module.sanitize_name("Token with non-ascii øłâ"), "Token with non-ascii øłâ")
        self.assertEqual(sniperx_v2_module.sanitize_name("VeryLongNameThatExceedsThirtyCharactersLimit"), "VeryLongNameThatExceedsThirtyC")
        self.assertEqual(sniperx_v2_module.sanitize_name(None, fallback_name="Fallback"), "Fallback")
        self.assertEqual(sniperx_v2_module.sanitize_name("None", fallback_name="Fallback"), "Fallback")
        self.assertEqual(sniperx_v2_module.sanitize_name(""), "Unknown")
        self.assertEqual(sniperx_v2_module.sanitize_name("  "), "Unknown")
        # Ensure this line uses the imported module and valid characters
        self.assertEqual(sniperx_v2_module.sanitize_name("\t\n "), "Unknown")
        # Use chr() for other control characters as well
        self.assertEqual(sniperx_v2_module.sanitize_name(f"{chr(0)}{chr(7)}{chr(11)}", fallback_name="Fallback"), "Fallback")

    @unittest.skipIf(sniperx_v2_module is None, "SniperX V2.py module not loaded")
    def test_get_token_metrics(self):
        data1 = [{"priceUsd": "0.123", "liquidity": {"usd": "12345.67"}, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data1)
        self.assertAlmostEqual(p, 0.123)
        self.assertAlmostEqual(l, 12345.67)
        self.assertAlmostEqual(v, 123.45)

        data2 = [{"price": "0.123", "liquidity": {"usd": "12345.67"}, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data2)
        self.assertAlmostEqual(p, 0.123)

        data3 = [{"priceUsd": "0.123", "liquidity": 12345.67, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data3)
        self.assertAlmostEqual(l, 12345.67)

        data4 = [{"priceUsd": "0.123", "liquidity": {"usd": "12345.67"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data4)
        self.assertAlmostEqual(v, 0.0)

        data5 = [{"priceUsd": "0.123", "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data5)
        self.assertAlmostEqual(l, 0.0)

        data6 = [{"liquidity": {"usd": "12345.67"}, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data6)
        self.assertAlmostEqual(p, 0.0)

        self.assertEqual(sniperx_v2_module.get_token_metrics([]), (0.0, 0.0, 0.0))
        self.assertEqual(sniperx_v2_module.get_token_metrics(None), (0.0, 0.0, 0.0))
        self.assertEqual(sniperx_v2_module.get_token_metrics([{}]), (0.0, 0.0, 0.0))

        data_malformed_price = [{"priceUsd": "not_a_float", "liquidity": {"usd": "12345.67"}, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data_malformed_price)
        self.assertAlmostEqual(p, 0.0)

        data_malformed_liq = [{"priceUsd": "0.123", "liquidity": {"usd": "not_a_float"}, "volume": {"m5": "123.45"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data_malformed_liq)
        self.assertAlmostEqual(l, 0.0)

        data_malformed_vol = [{"priceUsd": "0.123", "liquidity": {"usd": "12345.67"}, "volume": {"m5": "not_a_float"}}]
        p, l, v = sniperx_v2_module.get_token_metrics(data_malformed_vol)
        self.assertAlmostEqual(v, 0.0)

if __name__ == '__main__':
    unittest.main()
