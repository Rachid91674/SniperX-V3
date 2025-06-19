import asyncio
import websockets
import json
import aiohttp
import csv
import os
from pathlib import Path
import requests
import subprocess
import sys
from collections import deque
import time
from datetime import datetime

# --- Configuration ---
# Path to the CSV file used for monitoring. The location can be overridden with
# the `TOKEN_RISK_ANALYSIS_CSV` environment variable. When not set the
# `token_risk_analysis.csv` file located in the same directory as this script is
# used. This keeps the script portable across platforms.
SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_CSV_FILE = os.environ.get(
    "TOKEN_RISK_ANALYSIS_CSV",
    str(SCRIPT_DIR / "token_risk_analysis.csv"),
)
SOL_PRICE_UPDATE_INTERVAL_SECONDS = 30
TRADE_LOGIC_INTERVAL_SECONDS = 1
CSV_CHECK_INTERVAL_SECONDS = 10  # How often to check the CSV for changes
DEXSCREENER_PRICE_UPDATE_INTERVAL_SECONDS = 1  # How often to fetch price from Dexscreener when no trades
# STATUS_PRINT_INTERVAL_SECONDS removed (status now prints every loop iteration)

# --- Global State Variables ---
g_last_known_sol_price = 155.0
g_latest_trade_data = deque(maxlen=100)
# Timestamp (epoch seconds) of the last Dexscreener price request
g_last_dex_price_fetch_time = 0.0

g_current_mint_address = None
g_token_name = None
g_baseline_price_usd = None
g_trade_status = 'monitoring'  # Initial status
g_buy_price_usd = None

g_current_tasks = []  # Holds tasks like listener, trader, csv_checker for cancellation

# Custom exception for signaling restart
class RestartRequired(Exception):
    """Custom exception to signal a required restart of monitoring tasks."""
    pass

def log_trade_result(token_name, mint_address, reason, buy_price=None, sell_price=None):
    """Log trade results to a CSV file."""
    log_file = 'trades.csv'
    file_exists = os.path.isfile(log_file)
    
    with open(log_file, 'a', newline='') as f:
        fieldnames = ['timestamp', 'token_name', 'mint_address', 'reason', 'buy_price', 'sell_price']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
            
        writer.writerow({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'token_name': token_name,
            'mint_address': mint_address,
            'reason': reason,
            'buy_price': f"{buy_price:.9f}" if buy_price is not None else '',
            'sell_price': f"{sell_price:.9f}" if sell_price is not None else ''
        })

def restart_sniperx_v2():
    """Restart the SniperX V2 script and exit current process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, 'SniperX V2.py')
        
        if os.path.exists(script_path):
            # Clean up lock file if it exists
            lock_file = os.path.join(script_dir, 'monitoring_active.lock')
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                    print(f"🔓 Removed lock file: {lock_file}")
                except Exception as e:
                    print(f"⚠️ Could not remove lock file: {e}")
            
            # Start new process
            python_executable = sys.executable
            print(f"🔄 Restarting SniperX V2 script...")
            
            # Start the new process and exit current one
            if os.name == 'nt':  # Windows
                subprocess.Popen([python_executable, script_path], 
                              creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:  # Unix/Linux/Mac
                subprocess.Popen([python_executable, script_path],
                              start_new_session=True)
            
            # Exit the current process
            sys.exit(0)
            
        else:
            print(f"❌ SniperX V2.py not found at {script_path}")
            return False
    except Exception as e:
        print(f"❌ Failed to restart SniperX V2: {e}")
        return False
    return True

class TokenProcessingComplete(Exception):
    """Signals that the current token's monitoring/trading lifecycle is complete."""
    def __init__(self, mint_address, reason, buy_price=None, sell_price=None):
        self.mint_address = mint_address
        self.reason = reason
        self.buy_price = buy_price
        self.sell_price = sell_price
        # Log the trade result
        log_trade_result(g_token_name, mint_address, reason, buy_price, sell_price)
        print(f"🔄 Token processing complete. Restarting SniperX V2...")
        # Attempt to restart SniperX V2
        if restart_sniperx_v2():
            # If restart was successful, the process will exit before reaching here
            pass
        else:
            # If restart failed, raise the exception with the original message
            super().__init__(f"Token processing complete for {mint_address}: {reason}")
            # Still exit to ensure clean state
            sys.exit(0)

# --- Token Lifecycle Configuration ---
TAKE_PROFIT_THRESHOLD_PERCENT = 1.10  # e.g., 10% profit
STOP_LOSS_THRESHOLD_PERCENT = 0.95    # e.g., 5% loss
STAGNATION_PRICE_THRESHOLD_PERCENT = 0.80 # e.g., price is 20% below baseline
NO_BUY_SIGNAL_TIMEOUT_SECONDS = 180   # 3 minutes
STAGNATION_TIMEOUT_SECONDS = 180      # 3 minutes
BUY_SIGNAL_PRICE_INCREASE_PERCENT = 1.01 # 1% increase from baseline to consider it a buy signal
PRICE_IMPACT_THRESHOLD_MONITOR = 65.0  # Maximum price impact percentage to monitor (exclusive)

# --- Global State Variables (Token Lifecycle Specific) ---
g_token_start_time = None
g_buy_signal_detected = False
g_stagnation_timer_start = None # Tracks when price first fell below stagnation threshold

# --- CSV Loader ---
def load_token_from_csv(csv_file_path):
    """
    Loads the token from the CSV file.
    It prioritizes the *last* valid token entry in the CSV.
    A valid token entry must have a non-empty 'Address' field.
    """
    latest_mint_address = None
    latest_token_name = None
    try:
        with open(csv_file_path, mode='r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames:
                # This is not an error, CSV can be legitimately empty.
                # print(f"INFO: CSV '{csv_file_path}' is empty or has no headers. No token loaded.")
                return None, None

            actual_headers = [header.strip() for header in reader.fieldnames]
            if 'Address' not in actual_headers:
                print(f"Error: CSV missing 'Address' header. Found: {actual_headers}. No token loaded.")
                return None, None

            for row in reader:  # Iterate through all rows
                mint_address = row.get('Address', '').strip()

                # Filter on price impact - only monitor tokens with price impact < threshold (e.g., < 65%)
                # Try both with and without trailing underscore in column name for backward compatibility
                price_impact_str = row.get('Price_Impact_Cluster_Sell_Percent', row.get('Price_Impact_Cluster_Sell_Percent_', '')).strip()
                try:
                    price_impact_val = float(price_impact_str) if price_impact_str else PRICE_IMPACT_THRESHOLD_MONITOR + 1  # Default to exclude if missing
                except (ValueError, TypeError) as e:
                    print(f"Warning: Could not parse price impact value '{price_impact_str}': {e}")
                    continue

                if mint_address and price_impact_val < PRICE_IMPACT_THRESHOLD_MONITOR:
                    token_name_from_row = row.get('Name', '').strip()
                    latest_mint_address = mint_address
                    # Default name to address if 'Name' column is empty or not found for this row
                    latest_token_name = token_name_from_row if token_name_from_row else mint_address
            
            if latest_mint_address:
                # This print can be verbose if called every CSV_CHECK_INTERVAL_SECONDS by checker
                # Consider logging it only when a change is detected or at startup.
                # print(f"Loaded latest token from CSV: {latest_token_name} ({latest_mint_address})")
                return latest_mint_address, latest_token_name
            else:
                # print(f"INFO: No valid token with an 'Address' found in '{csv_file_path}'. No token loaded.")
                return None, None
                
    except FileNotFoundError:
        print(f"Error: Input CSV file '{csv_file_path}' not found. No token loaded.")
        return None, None
    except Exception as e:
        print(f"Error reading CSV file '{csv_file_path}': {e}. No token loaded.")
        return None, None

def remove_token_from_csv(mint_address_to_remove: str, csv_file_path: str):
    """Removes a token's row from the CSV file based on its mint address."""
    if not mint_address_to_remove:
        print("ERROR: No mint address provided for removal.")
        return False

    rows_to_keep = []
    headers = []
    found_and_removed = False

    try:
        with open(csv_file_path, mode='r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            headers = reader.fieldnames
            if not headers:
                print(f"INFO: CSV '{csv_file_path}' is empty or has no headers. Nothing to remove.")
                return False # Or True, depending on desired outcome for empty CSV
            
            if 'Address' not in headers:
                print(f"ERROR: CSV '{csv_file_path}' missing 'Address' header. Cannot remove token.")
                return False

            for row in reader:
                if row.get('Address', '').strip() == mint_address_to_remove:
                    found_and_removed = True
                    print(f"INFO: Token {mint_address_to_remove} marked for removal from '{csv_file_path}'.")
                else:
                    rows_to_keep.append(row)
        
        if not found_and_removed:
            print(f"INFO: Token {mint_address_to_remove} not found in '{csv_file_path}'. No changes made.")
            return False # Or True, as no error but nothing changed

        # Write the filtered rows back to the CSV
        with open(csv_file_path, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows_to_keep)
        
        print(f"INFO: Token {mint_address_to_remove} successfully removed from '{csv_file_path}'.")
        return True

    except FileNotFoundError:
        print(f"ERROR: CSV file '{csv_file_path}' not found during removal attempt.")
        return False
    except Exception as e:
        print(f"ERROR: Could not process CSV file '{csv_file_path}' for removal: {e}")
        return False

def reset_token_specific_state():
    """Resets global variables specific to the currently monitored token."""
    global g_latest_trade_data, g_baseline_price_usd, g_trade_status, g_buy_price_usd, \
           g_token_start_time, g_buy_signal_detected, g_stagnation_timer_start
    
    g_latest_trade_data.clear()
    g_baseline_price_usd = None
    g_trade_status = 'monitoring' # Reset to initial status
    g_buy_price_usd = None
    g_token_start_time = time.time()
    g_buy_signal_detected = False
    g_stagnation_timer_start = None
    print(f"Token-specific state reset. New token monitoring started at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(g_token_start_time))}.")

# --- SOL/USD Price Fetcher ---
async def periodic_sol_price_updater():
    """Periodically refresh the global SOL/USD price.

    Primary source: Pump.Fun.  Fallback: Coingecko simple price API.
    The function keeps the *same* update interval and preserves the previous
    behaviour if both endpoints fail – it just leaves the old cached value.
    """
    global g_last_known_sol_price

    primary_url = "https://frontend-api-v3.pump.fun/sol-price"
    secondary_url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

    async def fetch_json(session, url):
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception:
            return None

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                source_used = "Pump.Fun"
                data = await fetch_json(session, primary_url)
                price = None

                if data and isinstance(data, dict):
                    price = float(data.get("solPrice", 0)) or None

                # Fallback if primary failed or gave 0/None
                if not price:
                    source_used = "Coingecko"
                    data = await fetch_json(session, secondary_url)
                    if data and isinstance(data, dict):
                        price = float(data.get("solana", {}).get("usd", 0)) or None

                if price and price > 0:
                    if g_last_known_sol_price != price:
                        print(f"🔄 SOL/USD updated ({source_used}): {price:.2f} USD (was {g_last_known_sol_price:.2f} USD)")
                        g_last_known_sol_price = price
                else:
                    print(f"⚠️ Could not refresh SOL/USD price from either source, keeping last known: {g_last_known_sol_price:.2f} USD")

                await asyncio.sleep(SOL_PRICE_UPDATE_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                print("🔄 SOL/USD price updater task cancelled.")
                raise
            except Exception as e:
                print(f"❌ Unexpected error in SOL/USD price updater: {e}. Retrying in {SOL_PRICE_UPDATE_INTERVAL_SECONDS}s")
                await asyncio.sleep(SOL_PRICE_UPDATE_INTERVAL_SECONDS)

# --- Dexscreener Fallback Data Fetch ---
def get_dexscreener_data(token_address: str):
    """Fetch token price & volume info from Dexscreener.

    It first tries the legacy token-pairs endpoint and, if that fails or returns
    no useful data, falls back to the newer `latest/dex/tokens` endpoint.
    Returns dict with keys: price, buy_volume, sell_volume, buys, sells or None
    on failure.
    """
    endpoints = [
        # Legacy (sometimes still works for Solana)
        f"https://api.dexscreener.com/token-pairs/v1/solana/{token_address}",
        # Newer universal endpoint
        f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
    ]

    for idx, url in enumerate(endpoints, 1):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 404:
                continue  # Not found on this endpoint, try next
            res.raise_for_status()
            data = res.json()

            # Normalise to list of pairs
            if isinstance(data, dict):
                pairs = data.get("pairs") or []
            elif isinstance(data, list):
                pairs = data
            else:
                print(f"Dexscreener endpoint {idx} returned unexpected type {type(data)} for {token_address}")
                continue

            if not pairs:
                continue  # No pools here, try next endpoint

            for pair_data in pairs:
                try:
                    if not (pair_data.get("txns") and pair_data["txns"].get("h1") and \
                            pair_data.get("volume") and pair_data["volume"].get("h1") and \
                            pair_data.get("priceUsd")):
                        continue

                    buy_txns = int(pair_data["txns"]["h1"].get("buys", 0))
                    sell_txns = int(pair_data["txns"]["h1"].get("sells", 0))
                    total_hourly_volume_usd = float(pair_data["volume"]["h1"])
                    total_txns = buy_txns + sell_txns

                    buy_volume_estimation = sell_volume_estimation = 0.0
                    if total_txns > 0:
                        buy_share = buy_txns / total_txns
                        sell_share = sell_txns / total_txns
                        buy_volume_estimation = total_hourly_volume_usd * buy_share
                        sell_volume_estimation = total_hourly_volume_usd * sell_share

                    return {
                        "price": float(pair_data["priceUsd"]),
                        "buy_volume": buy_volume_estimation,
                        "sell_volume": sell_volume_estimation,
                        "buys": buy_txns,
                        "sells": sell_txns,
                    }
                except (KeyError, ValueError, TypeError):
                    continue

            # If we got here, endpoint had pairs but none usable
        except requests.RequestException as e_req:
            print(f"❌ Dexscreener request error (endpoint {idx}) for {token_address}: {e_req}")
        except json.JSONDecodeError:
            print(f"❌ Dexscreener response not valid JSON (endpoint {idx}) for {token_address}.")
        except Exception as e:
            print(f"❌ Unexpected Dexscreener error (endpoint {idx}) for {token_address}: {e}")

    # None of the endpoints produced usable data
    return None

# --- WebSocket Listener ---
async def listen_for_trades(mint_address_to_monitor):
    global g_latest_trade_data, g_token_name
    uri = "wss://pumpportal.fun/api/data"

    # Preserve the token name for logging in case globals change during a restart
    current_task_token_name = g_token_name

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                subscribe_payload = {
                    "method": "subscribeTokenTrade",
                    "keys": [mint_address_to_monitor],
                }
                await websocket.send(json.dumps(subscribe_payload))
                print(
                    f"📡 Subscribed to trades for token: {current_task_token_name} ({mint_address_to_monitor})"
                )

                async for message in websocket:
                    data = json.loads(message)
                    if data.get("mint") != mint_address_to_monitor:
                        continue

                    if "tokenAmount" in data and "solAmount" in data:
                        try:
                            trade = {
                                "amount": float(data["tokenAmount"]),
                                "solAmount": float(data["solAmount"]),
                                "side": data.get("side", "").lower(),
                                "wallet": data.get("wallet", ""),
                            }
                            g_latest_trade_data.append(trade)
                        except ValueError:
                            print(
                                f"❌ WebSocket received non-numeric trade data for {current_task_token_name}: {data}"
                            )
        except websockets.exceptions.ConnectionClosed as e:
            print(
                f"🔌 WebSocket connection closed for {current_task_token_name}: {e}. Reconnecting in 5 seconds..."
            )
        except asyncio.CancelledError:
            print(f"📡 WebSocket listener for {current_task_token_name} cancelled.")
            raise
        except Exception as e:
            print(
                f"❌ WebSocket Error for {current_task_token_name}: {e}. Reconnecting in 5 seconds..."
            )

        # Any exception besides cancellation triggers a short delay before reconnecting
        await asyncio.sleep(5)


# --- Trade Logic ---
async def trade_logic_and_price_display_loop():
    global g_baseline_price_usd, g_trade_status, g_buy_price_usd, g_token_name, g_current_mint_address, \
           g_token_start_time, g_buy_signal_detected, g_stagnation_timer_start, g_last_dex_price_fetch_time

    current_task_token_name = g_token_name
    current_task_mint_address = g_current_mint_address
    print(f"📈 Starting trade logic for {current_task_token_name or current_task_mint_address}")


    try:
        while True:
            if g_current_mint_address != current_task_mint_address:
                print(f"📈 Trade logic for old token {current_task_token_name} is stale. Exiting task.")
                return

            current_time = time.time()
            usd_price_per_token = None
            
            # --------------- Price Determination ---------------
            # 1) Preferred: price from the most recent trade (if any)
            if g_latest_trade_data:
                recent_trade = g_latest_trade_data[-1]
                token_amount = recent_trade["amount"]
                sol_amount = recent_trade["solAmount"]
                if token_amount > 0:
                    sol_price_per_token = sol_amount / token_amount
                    usd_price_per_token = sol_price_per_token * g_last_known_sol_price

            # 2) Fallback: query Dexscreener if we still have no price and enough time passed
            if usd_price_per_token is None and (current_time - g_last_dex_price_fetch_time) > DEXSCREENER_PRICE_UPDATE_INTERVAL_SECONDS:
                loop = asyncio.get_running_loop()
                dex_data = await loop.run_in_executor(None, get_dexscreener_data, current_task_mint_address)
                if dex_data and dex_data.get("price"):
                    usd_price_per_token = float(dex_data["price"])
                g_last_dex_price_fetch_time = current_time

            # --- Initial Baseline Price Setting ---
            if usd_price_per_token is not None and g_baseline_price_usd is None:
                g_baseline_price_usd = usd_price_per_token
                print(f"💰 Initial Baseline Price for {current_task_token_name}: {g_baseline_price_usd:.9f} USD")

            # --- Token Lifecycle Checks (only if baseline is set) ---
            if g_baseline_price_usd is not None:
                # 1. No Buy Signal Timeout Check
                if not g_buy_signal_detected:
                    if usd_price_per_token is not None and usd_price_per_token > g_baseline_price_usd * BUY_SIGNAL_PRICE_INCREASE_PERCENT:
                        g_buy_signal_detected = True
                        g_buy_price_usd = usd_price_per_token # Set buy price at the point of signal
                        g_trade_status = 'bought' # Update status
                        print(f"🚨 BUY SIGNAL DETECTED for {current_task_token_name} at {g_buy_price_usd:.9f} USD (Baseline: {g_baseline_price_usd:.9f} USD)")
                    elif (current_time - g_token_start_time) > NO_BUY_SIGNAL_TIMEOUT_SECONDS:
                        print(f"⏳ NO BUY SIGNAL timeout for {current_task_token_name} after {NO_BUY_SIGNAL_TIMEOUT_SECONDS}s. Baseline: {g_baseline_price_usd:.9f} USD.")
                        raise TokenProcessingComplete(
                            current_task_mint_address, 
                            "No buy signal timeout",
                            sell_price=usd_price_per_token
                        )

                # 2. Take Profit / Stop Loss Checks (only if buy signal was detected)
                if g_buy_signal_detected and g_buy_price_usd is not None and usd_price_per_token is not None:
                    if usd_price_per_token >= g_buy_price_usd * TAKE_PROFIT_THRESHOLD_PERCENT:
                        print(f"✅ TAKE PROFIT for {current_task_token_name} at {usd_price_per_token:.9f} USD (Target: {g_buy_price_usd * TAKE_PROFIT_THRESHOLD_PERCENT:.9f}, Buy: {g_buy_price_usd:.9f} USD)")
                        raise TokenProcessingComplete(
                            current_task_mint_address, 
                            "Take profit",
                            buy_price=g_buy_price_usd,
                            sell_price=usd_price_per_token
                        )
                    
                    if usd_price_per_token <= g_buy_price_usd * STOP_LOSS_THRESHOLD_PERCENT:
                        print(f"🛑 STOP LOSS for {current_task_token_name} at {usd_price_per_token:.9f} USD (Target: {g_buy_price_usd * STOP_LOSS_THRESHOLD_PERCENT:.9f}, Buy: {g_buy_price_usd:.9f} USD)")
                        raise TokenProcessingComplete(
                            current_task_mint_address, 
                            "Stop loss",
                            buy_price=g_buy_price_usd,
                            sell_price=usd_price_per_token
                        )

                # 3. Stagnation Check (applies whether buy signal occurred or not, based on baseline)
                if usd_price_per_token is not None:
                    if usd_price_per_token < g_baseline_price_usd * STAGNATION_PRICE_THRESHOLD_PERCENT:
                        if g_stagnation_timer_start is None:
                            g_stagnation_timer_start = current_time # Start timer
                            print(f"📉 Price for {current_task_token_name} ({usd_price_per_token:.9f}) fell below stagnation threshold ({g_baseline_price_usd * STAGNATION_PRICE_THRESHOLD_PERCENT:.9f}). Stagnation timer started.")
                        elif (current_time - g_stagnation_timer_start) > STAGNATION_TIMEOUT_SECONDS:
                            print(f"⏳ STAGNATION TIMEOUT for {current_task_token_name}. Price ({usd_price_per_token:.9f}) remained below {g_baseline_price_usd * STAGNATION_PRICE_THRESHOLD_PERCENT:.9f} for {STAGNATION_TIMEOUT_SECONDS}s.")
                            raise TokenProcessingComplete(
                                current_task_mint_address, 
                                "Stagnation timeout",
                                sell_price=usd_price_per_token
                            )
                    else: # Price is above stagnation threshold
                        if g_stagnation_timer_start is not None:
                            print(f"📈 Price for {current_task_token_name} ({usd_price_per_token:.9f}) recovered above stagnation threshold. Resetting stagnation timer.")
                            g_stagnation_timer_start = None # Reset timer if price recovers
                elif g_stagnation_timer_start is None and (current_time - g_token_start_time) > STAGNATION_TIMEOUT_SECONDS: # No price data at all for stagnation period
                     print(f"⏳ STAGNATION TIMEOUT for {current_task_token_name} due to no price data for {STAGNATION_TIMEOUT_SECONDS}s while monitoring baseline {g_baseline_price_usd:.9f}.")
                     raise TokenProcessingComplete(
                         current_task_mint_address, 
                         "Stagnation timeout - no price data"
                     )

            # Display current status (can be made more concise or less frequent)
            if usd_price_per_token is not None:
                status_msg = (f"[{time.strftime('%H:%M:%S')}] Status for {current_task_token_name}: "
                              f"Price={usd_price_per_token:.9f} USD, "
                              f"Baseline={g_baseline_price_usd:.9f} USD, "
                              f"BuySignal={g_buy_signal_detected}")
                if g_buy_price_usd:
                    status_msg += f", BuyPrice={g_buy_price_usd:.9f} USD"
                print(status_msg)
            elif g_baseline_price_usd is None:
                # print(f"Waiting for first trade data for {current_task_token_name} to set baseline...")
                pass

            await asyncio.sleep(TRADE_LOGIC_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        print(f"📈 Trade logic for {current_task_token_name} cancelled.")
        raise

# --- CSV Checker and Restart Trigger ---
async def periodic_csv_checker():
    global g_current_mint_address, g_token_name, g_current_tasks
    
    address_being_monitored_by_this_task = g_current_mint_address 
    name_being_monitored_by_this_task = g_token_name

    try:
        while True:
            await asyncio.sleep(CSV_CHECK_INTERVAL_SECONDS)
            
            # This task might be for an "old" token if globals changed.
            # Check if this checker instance is still relevant.
            if g_current_mint_address != address_being_monitored_by_this_task:
                print(f"📋 CSV checker for old token {name_being_monitored_by_this_task} is stale. Exiting task.")
                return # Exit if this checker is for a token no longer actively monitored by main loop

            new_target_mint_address, new_target_token_name = load_token_from_csv(INPUT_CSV_FILE)

            if new_target_mint_address:
                if new_target_mint_address != address_being_monitored_by_this_task:
                    print(f"🔄 CSV Change Detected: New target '{new_target_token_name}' ({new_target_mint_address}). "
                          f"Current is '{name_being_monitored_by_this_task}' ({address_being_monitored_by_this_task}). Triggering restart.")
                    for task in g_current_tasks:
                        if task is not asyncio.current_task():
                            task.cancel()
                    raise RestartRequired()
            else: 
                if address_being_monitored_by_this_task is not None:
                    print(f"🔄 CSV Change Detected: No target token in CSV. "
                          f"Previously monitoring '{name_being_monitored_by_this_task}' ({address_being_monitored_by_this_task}). Triggering stop/restart.")
                    for task in g_current_tasks:
                        if task is not asyncio.current_task():
                            task.cancel()
                    raise RestartRequired()
    except asyncio.CancelledError:
        print(f"📋 CSV checker for {name_being_monitored_by_this_task} cancelled.")
        raise


# --- Main Runner ---
async def main():
    global g_current_mint_address, g_token_name, g_current_tasks

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    LOCK_FILE_PATH = os.path.join(SCRIPT_DIR, "monitoring_active.lock")

    # Initial check: if lock file exists and we are trying to start, it's an issue.
    # This check is done *before* attempting to create our own lock file.
    # Note: The startup logic in `if __name__ == "__main__"` also tries to clear stale locks.
    # This check here is a secondary defense or for cases where that might not have run.
    if os.path.exists(LOCK_FILE_PATH):
        pid_str = ""
        try:
            with open(LOCK_FILE_PATH, 'r') as f_lock_read:
                pid_str = f_lock_read.read().strip()
            # Simple check: if it exists, it's problematic unless it's an empty file from a crash
            print(f"ERROR: Lock file {LOCK_FILE_PATH} exists (PID in file: '{pid_str}'). Another instance of Monitoring.py might be running or it's a stale lock.")
            print("The script will attempt to run, but if issues persist, manually remove the lock file.")
            # Not returning here to allow the startup stale lock removal to take precedence if it can.
        except Exception as e_lock_read:
            print(f"ERROR: Lock file {LOCK_FILE_PATH} exists but could not read PID: {e_lock_read}. It might be a stale lock.")
            # Not returning, let the script attempt to manage it.

    sol_price_task = None
    lock_created_by_this_instance = False
    try:
        # Attempt to create the lock file for this instance
        # This action signifies that this instance is now intending to be active.
        with open(LOCK_FILE_PATH, 'w') as f_lock:
            f_lock.write(str(os.getpid()))
        lock_created_by_this_instance = True
        print(f"INFO: Lock file {LOCK_FILE_PATH} created/taken by Monitoring.py (PID: {os.getpid()}).")

        sol_price_task = asyncio.create_task(periodic_sol_price_updater())

        while True:
            loaded_mint_address, loaded_token_name = load_token_from_csv(INPUT_CSV_FILE)

            if not loaded_mint_address:
                if g_current_mint_address is not None: # If we were monitoring something and it disappeared
                    print(f"INFO: No token currently specified in '{INPUT_CSV_FILE}'. Ceasing monitoring of '{g_token_name}'.")
                
                # If no token is active, this instance should not hold the lock.
                if lock_created_by_this_instance and os.path.exists(LOCK_FILE_PATH):
                    # print(f"INFO: No token active, ensuring lock file {LOCK_FILE_PATH} (owned by PID {os.getpid()}) is removed.")
                    try:
                        # Before removing, ideally verify it's still our lock (e.g. check PID in file)
                        # For now, if this instance created it and it exists, remove it.
                        current_pid_in_lock = ""
                        try:
                            with open(LOCK_FILE_PATH, 'r') as f_check: current_pid_in_lock = f_check.read().strip()
                        except: pass
                        if current_pid_in_lock == str(os.getpid()):
                            os.remove(LOCK_FILE_PATH)
                            print(f"🔑 Lock file {LOCK_FILE_PATH} removed as no token is being monitored by this instance.")
                        elif os.path.exists(LOCK_FILE_PATH): # Lock exists but not ours
                             print(f"INFO: No token active, but lock file {LOCK_FILE_PATH} (PID: {current_pid_in_lock}) is not from this instance. Leaving it.")
                    except OSError as e:
                        print(f"ERROR: Could not remove own lock file {LOCK_FILE_PATH} during no-token state: {e}")
                
                print(f"INFO: No token found in '{INPUT_CSV_FILE}' to monitor. Will check again in {CSV_CHECK_INTERVAL_SECONDS}s.")
                
                g_current_mint_address = None 
                g_token_name = None
                
                if g_current_tasks:
                    for task in g_current_tasks:
                        if not task.done(): task.cancel()
                    await asyncio.gather(*g_current_tasks, return_exceptions=True)
                    g_current_tasks = []

                await asyncio.sleep(CSV_CHECK_INTERVAL_SECONDS)
                continue

            # A token IS loaded. Ensure lock file exists if this instance is supposed to hold it.
            if lock_created_by_this_instance and not os.path.exists(LOCK_FILE_PATH):
                print(f"INFO: Token '{loaded_token_name}' is active. Re-asserting lock file {LOCK_FILE_PATH} for PID {os.getpid()}.")
                try:
                    with open(LOCK_FILE_PATH, 'w') as f_lock:
                        f_lock.write(str(os.getpid()))
                    print(f"INFO: Lock file {LOCK_FILE_PATH} re-created/taken by Monitoring.py (PID: {os.getpid()}).")
                except Exception as e_recreate_lock:
                    print(f"ERROR: Could not re-create lock file {LOCK_FILE_PATH} for active token: {e_recreate_lock}")
            
            # --- Token Monitoring Logic --- 
            if g_current_mint_address != loaded_mint_address or not g_current_tasks:
                if g_current_tasks: # Clean up tasks for old token or if tasks ended prematurely
                    # print(f"INFO: Token changed or tasks ended. Cleaning up for '{g_token_name or 'previous'}'.")
                    for task in g_current_tasks:
                        if not task.done(): task.cancel()
                    await asyncio.gather(*g_current_tasks, return_exceptions=True)
                
                g_current_mint_address = loaded_mint_address
                g_token_name = loaded_token_name
                
                print(f"🚀 Initializing/Re-initializing monitoring for: {g_token_name} ({g_current_mint_address})")
                reset_token_specific_state()

                listener_task = asyncio.create_task(listen_for_trades(g_current_mint_address))
                trader_task = asyncio.create_task(trade_logic_and_price_display_loop())
                csv_checker_task = asyncio.create_task(periodic_csv_checker())
                
                g_current_tasks = [listener_task, trader_task, csv_checker_task]

            # Await tasks and handle their outcomes
            token_processing_outcome = None # Can be 'completed', 'restart_required', 'error', or None
            processed_token_mint_address = g_current_mint_address # Capture before tasks might alter globals
            processed_token_name = g_token_name
            
            if g_current_tasks:
                results = await asyncio.gather(*g_current_tasks, return_exceptions=True)

                for res_idx, result in enumerate(results):
                    current_task_obj = g_current_tasks[res_idx] if res_idx < len(g_current_tasks) else None
                    task_name_for_log = current_task_obj.get_name() if hasattr(current_task_obj, 'get_name') else f"Task-{res_idx}"

                    if isinstance(result, TokenProcessingComplete):
                        print(f"✅ Token processing complete for {result.mint_address or processed_token_name}: {result.reason}")
                        remove_token_from_csv(result.mint_address or processed_token_mint_address, INPUT_CSV_FILE)
                        token_processing_outcome = 'completed'
                        break 
                    elif isinstance(result, RestartRequired):
                        print(f"🔄 RestartRequired signal received from task '{task_name_for_log}' for token {processed_token_name}.")
                        token_processing_outcome = 'restart_required'
                        break
                    elif isinstance(result, asyncio.CancelledError):
                        # This is often an expected outcome if another task triggered completion/restart
                        # print(f"DEBUG: Task '{task_name_for_log}' for {processed_token_name} was cancelled.")
                        pass 
                    elif isinstance(result, Exception):
                        print(f"💥 Unexpected error in task '{task_name_for_log}' for token {processed_token_name}: {result}")
                        # import traceback # Already imported at top level if needed elsewhere
                        # traceback.print_exc() # Consider logging level for this
                        token_processing_outcome = 'error'
                        # Decide if token should be removed on generic error to prevent loops
                        # print(f"INFO: Token {processed_token_mint_address} will be retried or re-evaluated. Not removing from CSV on generic error.")
                        break
            
            # Cleanup tasks for the token that was just processed (or attempted)
            active_tasks_to_await_cleanup = []
            for task in g_current_tasks:
                if task and not task.done():
                    task.cancel()
                    active_tasks_to_await_cleanup.append(task)
            if active_tasks_to_await_cleanup:
                await asyncio.gather(*active_tasks_to_await_cleanup, return_exceptions=True)
            
            g_current_tasks = [] # Clear tasks list for the next iteration
            # Reset global state associated with the token that just finished. 
            # load_token_from_csv and reset_token_specific_state will handle the next token.
            g_current_mint_address = None 
            g_token_name = None

            if token_processing_outcome == 'restart_required':
                print(f"INFO: Propagating RestartRequired for full script restart.")
                raise RestartRequired() 
            elif token_processing_outcome == 'completed' or token_processing_outcome == 'error':
                reason_for_cycle = token_processing_outcome if token_processing_outcome else "unknown reasons"
                print(f"INFO: Cycling to next token after '{reason_for_cycle}' for {processed_token_name or processed_token_mint_address}.")
                await asyncio.sleep(0.1) # Brief pause before reloading CSV
                continue # To the start of the main while True loop to load next token
            else: # No specific outcome (e.g., tasks finished without exceptions, or g_current_tasks was empty)
                  # This path might also be taken if all tasks were cancelled externally before gather completed
                # print(f"DEBUG: No specific token outcome for {processed_token_name}, proceeding in main loop.")
                await asyncio.sleep(0.1) # Brief pause
                continue 

    except KeyboardInterrupt:
        print("\n📉 Monitoring stopped by user (KeyboardInterrupt).")
    except asyncio.CancelledError:
        print("\n🌀 Main task was cancelled. Shutting down.")
    except Exception as e_main:
        print(f"\n💥 An unexpected error occurred in main: {e_main}")
        import traceback
        traceback.print_exc()
    finally:
        print("INFO: Shutting down Monitoring.py...")
        if sol_price_task and not sol_price_task.done():
            sol_price_task.cancel()
            try: await sol_price_task
            except asyncio.CancelledError: pass
            except Exception as e_sol_cancel: print(f"ERROR cancelling SOL price task: {e_sol_cancel}")

        # Final cleanup for any g_current_tasks during shutdown
        final_shutdown_tasks = []
        for task in g_current_tasks:
            if task and not task.done(): task.cancel(); final_shutdown_tasks.append(task)
        if final_shutdown_tasks: await asyncio.gather(*final_shutdown_tasks, return_exceptions=True)

        if lock_created_by_this_instance and os.path.exists(LOCK_FILE_PATH):
            try:
                # Verify it's our lock file by checking PID before removing
                current_pid_in_lock_on_exit = ""
                try: 
                    with open(LOCK_FILE_PATH, 'r') as f_check_exit: current_pid_in_lock_on_exit = f_check_exit.read().strip()
                except: pass
                if current_pid_in_lock_on_exit == str(os.getpid()):
                    os.remove(LOCK_FILE_PATH)
                    print(f"INFO: Lock file {LOCK_FILE_PATH} (PID: {os.getpid()}) removed by Monitoring.py upon exit.")
                elif os.path.exists(LOCK_FILE_PATH): # Lock exists but not ours
                    print(f"INFO: Lock file {LOCK_FILE_PATH} (PID: {current_pid_in_lock_on_exit}) was not removed as it's not owned by this instance (PID: {os.getpid()}).")
            except Exception as e_lock_remove:
                print(f"ERROR: Monitoring.py could not remove its lock file {LOCK_FILE_PATH} on exit: {e_lock_remove}")

if __name__ == "__main__":
    # Define SCRIPT_DIR and LOCK_FILE_PATH here as well for the initial stale check
    # This is important because main() might not be called if there's an early exit.
    _SCRIPT_DIR_MAIN = os.path.dirname(os.path.abspath(__file__))
    _LOCK_FILE_PATH_MAIN = os.path.join(_SCRIPT_DIR_MAIN, "monitoring_active.lock")

    if os.path.exists(_LOCK_FILE_PATH_MAIN):
        try:
            # Simple removal, no PID check here. If it's there, it's considered stale at this point.
            print(f"INFO: Stale lock file {_LOCK_FILE_PATH_MAIN} found on script startup. Attempting removal.")
            os.remove(_LOCK_FILE_PATH_MAIN)
            print(f"🔑 Stale lock file {_LOCK_FILE_PATH_MAIN} removed successfully.")
        except OSError as e:
            print(f"WARNING: Could not remove stale lock file {_LOCK_FILE_PATH_MAIN} on startup: {e}. Manual check may be needed if script fails to start.")

    try:
        asyncio.run(main())
    except Exception as e_run:
        print(f"\n💥 An critical error occurred at asyncio.run level: {e_run}")
        import traceback
        traceback.print_exc()
    finally:
        print("INFO: Monitoring.py has shut down completely.")