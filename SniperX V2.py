#!/usr/bin/env python3

import os
import requests
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
import dateparser
import csv      # Added for CSV writing
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time
from dotenv import load_dotenv
import subprocess
import logging
import signal

def load_environment():
    """Load environment variables and verify configuration."""
    env_file = None
    if os.path.exists('.env'):
        env_file = '.env'
        print(f"[INFO] Loading environment from: {os.path.abspath('.env')}")
        load_dotenv(dotenv_path=env_file)
    elif os.path.exists('sniperx_config.env'):
        env_file = 'sniperx_config.env'
        print(f"[INFO] Loading environment from: {os.path.abspath('sniperx_config.env')}")
        load_dotenv(dotenv_path=env_file)
    else:
        print("[ERROR] No environment file found. Please ensure either .env or sniperx_config.env exists.")
        sys.exit(1)
    
    # Verify the environment file contents
    if env_file:
        try:
            print(f"\n[DEBUG] Contents of {env_file}:")
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Mask sensitive values
                        if any(key in line.upper() for key in ['API_KEY', 'TOKEN', 'SECRET', 'PASSWORD']):
                            parts = line.split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                print(f"  {key}=[MASKED]")
                                continue
                        print(f"  {line}")
            print()
        except Exception as e:
            print(f"[WARN] Could not read environment file: {e}")

# Load and verify environment
load_environment()

# --- Global Configurations & Constants ---
TEST_MODE = len(sys.argv) > 1 and sys.argv[1] == "--test"

# Debug: Print all environment variables starting with MORALIS_API_KEY_
print("\n[DEBUG] Checking Moralis API keys in environment:")
for k, v in os.environ.items():
    if k.startswith('MORALIS_API_KEY_'):
        print(f"  {k}: {'*' * 8}{v[-4:] if v else 'None'}")

# Load API keys from environment
MORALIS_API_KEYS = [os.getenv(f"MORALIS_API_KEY_{i}", "").strip('"\'') for i in range(1,6)]
MORALIS_API_KEYS = [k.split('#')[0].strip() for k in MORALIS_API_KEYS]  # Remove comments
MORALIS_API_KEYS = [k for k in MORALIS_API_KEYS if k]  # Filter out empty keys

# Standard Moralis API endpoint (updated to use the standard API endpoint)
MORALIS_API_BASE = "https://deep-index.moralis.io/api/v2"

print(f"\n[DEBUG] Loaded {len(MORALIS_API_KEYS)} Moralis API keys")
for i, key in enumerate(MORALIS_API_KEYS, 1):
    print(f"  Key {i}: {'*' * 8}{key[-4:] if key else 'None'}")

if not MORALIS_API_KEYS:
    print("\n[ERROR] No valid Moralis API keys found in environment variables. Please check your configuration.")
    print("Make sure your sniperx_config.env file contains valid MORALIS_API_KEY_1, MORALIS_API_KEY_2, etc. entries.")
    sys.exit(1)

# Update the exchange name and API URL
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "pumpfun")
MORALIS_API_URL = "https://deep-index.moralis.io/api/v2/solana/token/graduated"
DEFAULT_MAX_WORKERS = 1 # <<<< SET TO 1 AS PER USER REQUEST >>>>
DEXSCREENER_CHAIN_ID = "solana"
raw_snipe_age_env = os.getenv("SNIPE_GRADUATED_DELTA_MINUTES", "60")
SNIPE_GRADUATED_DELTA_MINUTES_FLOAT = float(raw_snipe_age_env.split('#')[0].strip()) if raw_snipe_age_env else 60.0
raw_wt = os.getenv("WHALE_TRAP_WINDOW_MINUTES", "1,5")
raw_wt = raw_wt.split('#')[0].strip()
parts = raw_wt.split(',')
WINDOW_MINS = []
for part in parts:
    try:
        val = int(part.strip())
        if val > 0 : WINDOW_MINS.append(val)
    except: pass
if not WINDOW_MINS: WINDOW_MINS = [1, 5]

raw_pl = os.getenv("PRELIM_LIQUIDITY_THRESHOLD", "5000").split('#')[0].strip()
PRELIM_LIQUIDITY_THRESHOLD = float(raw_pl) if raw_pl else 5000.0
raw_ppmin = os.getenv("PRELIM_MIN_PRICE_USD", "0.00001").split('#')[0].strip()
PRELIM_MIN_PRICE_USD = float(raw_ppmin) if raw_ppmin else 0.00001
raw_ppmax = os.getenv("PRELIM_MAX_PRICE_USD", "0.0004").split('#')[0].strip()
PRELIM_MAX_PRICE_USD = float(raw_ppmax) if raw_ppmax else 0.0004
raw_age = os.getenv("PRELIM_AGE_DELTA_MINUTES", "120").split('#')[0].strip()
PRELIM_AGE_DELTA_MINUTES = float(raw_age) if raw_age else 120.0
raw_wp = os.getenv("WHALE_PRICE_UP_PCT", "0.0").split('#')[0].strip()
WHALE_PRICE_UP_PCT = float(raw_wp) if raw_wp else 0.0
raw_wlq = os.getenv("WHALE_LIQUIDITY_UP_PCT", "0.0").split('#')[0].strip()
WHALE_LIQUIDITY_UP_PCT = float(raw_wlq) if raw_wlq else 0.0
raw_wvd = os.getenv("WHALE_VOLUME_DOWN_PCT", "0.0").split('#')[0].strip()
WHALE_VOLUME_DOWN_PCT = float(raw_wvd) if raw_wvd else 0.0
raw_sl1 = os.getenv("SNIPE_LIQUIDITY_MIN_PCT_1M","0.1").split('#')[0].strip()
SNIPE_LIQUIDITY_MIN_PCT_1M = float(raw_sl1) if raw_sl1 else 0.1
raw_sm1 = os.getenv("SNIPE_LIQUIDITY_MULTIPLIER_1M","1.5").split('#')[0].strip()
SNIPE_LIQUIDITY_MULTIPLIER_1M = float(raw_sm1) if raw_sm1 else 1.5
raw_sup = os.getenv("SNIPE_LIQUIDITY_UP_PCT", "0.30").split('#')[0].strip()
SNIPE_LIQUIDITY_UP_PCT_CONFIG = float(raw_sup) if raw_sup else 0.30
raw_sl5 = os.getenv("SNIPE_LIQUIDITY_MIN_PCT_5M", str(SNIPE_LIQUIDITY_UP_PCT_CONFIG)).split('#')[0].strip()
SNIPE_LIQUIDITY_MIN_PCT_5M = float(raw_sl5) if raw_sl5 else SNIPE_LIQUIDITY_UP_PCT_CONFIG
raw_sm5 = os.getenv("SNIPE_LIQUIDITY_MULTIPLIER_5M","5").split('#')[0].strip()
SNIPE_LIQUIDITY_MULTIPLIER_5M = float(raw_sm5) if raw_sm5 else 5.0
raw_gv1 = os.getenv("GHOST_VOLUME_MIN_PCT_1M", "0.5").split('#')[0].strip()
GHOST_VOLUME_MIN_PCT_1M = float(raw_gv1) if raw_gv1 else 0.5
raw_gv5 = os.getenv("GHOST_VOLUME_MIN_PCT_5M", "0.5").split('#')[0].strip()
GHOST_VOLUME_MIN_PCT_5M = float(raw_gv5) if raw_gv5 else 0.5
raw_gpr = os.getenv("GHOST_PRICE_REL_MULTIPLIER", "2").split('#')[0].strip()
GHOST_PRICE_REL_MULTIPLIER = float(raw_gpr) if raw_gpr else 2.0


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

def get_graduated_tokens():
    print(f"\n[DEBUG] ===== Starting get_graduated_tokens() =====")
    print(f"[DEBUG] Using EXCHANGE_NAME: {EXCHANGE_NAME}")
    print(f"[DEBUG] Full API URL: {MORALIS_API_URL}")
    
    print(f"[INFO] Fetching graduated tokens from '{EXCHANGE_NAME}'...")
    
    for idx, key in enumerate(MORALIS_API_KEYS, start=1):
        # Mask the key for security (only show first 8 and last 4 chars)
        key_display = f"{key[:8]}...{key[-4:] if len(key) > 12 else ''}" if key else "[EMPTY KEY]"
        print(f"\n[INFO] Trying Moralis API key {idx}/{len(MORALIS_API_KEYS)}: {key_display}")
        
        # Updated headers for Moralis API v2
        headers = {
            "Accept": "application/json",
            "X-API-Key": key,
            "Content-Type": "application/json"
        }
        
        params = {
            'limit': 100,
            'exchange': EXCHANGE_NAME,
            'chain': 'solana',
            'order': 'DESC',
            'from_date': (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()  # Last 24 hours
        }
        
        try:
            print("[DEBUG] Sending request to Moralis API...")
            resp = requests.get(MORALIS_API_URL, headers=headers, params=params, timeout=20)
            print(f"[DEBUG] Response status code: {resp.status_code}")
            
            # Log response headers for debugging
            print("[DEBUG] Response headers:", dict(resp.headers))
            
            resp.raise_for_status()
            
            data = resp.json()
            
            # Handle different response formats
            if isinstance(data, list):
                tokens = data
            elif isinstance(data, dict):
                tokens = data.get('result', [])
            else:
                tokens = []
                
            print(f"[INFO] Success! Retrieved {len(tokens)} tokens with key {idx}.")
            return [t for t in tokens if isinstance(t, dict)] 
            
        except requests.exceptions.HTTPError as err:
            status = getattr(err.response, 'status_code', None)
            error_details = ""
            if hasattr(err.response, 'text'):
                error_details = f" Response: {err.response.text}"
            print(f"[WARN] Key {idx} HTTP {status} error: {err}.{error_details}")
            
            # If we get a 401, the key is definitely invalid
            if status == 401:
                print("[ERROR] This API key appears to be invalid. Please check your Moralis account.")
            
        except requests.exceptions.RequestException as err:
            print(f"[WARN] Request error with key {idx}: {err}")
            
        except ValueError as ve:
            print(f"[WARN] JSON decode error with key {idx}: {ve}")
            if hasattr(resp, 'text'):
                print(f"[DEBUG] Response content: {resp.text[:200]}...")
        
        print(f"[INFO] Trying next API key...")
    
    print("\n[ERROR] All Moralis API keys failed. Possible issues:")
    print("  1. API keys might be invalid or expired")
    print(f"  2. Check if the endpoint is correct: {MORALIS_API_URL}")
    print("  3. Verify your Moralis account has access to the requested exchange")
    print("  4. Check if you've exceeded your API rate limits")
    print("\n[ACTION REQUIRED] Please check your Moralis dashboard to:")
    print("  1. Verify your API keys are correct")
    print("  2. Check if your subscription is active")
    print("  3. Ensure you have access to the Solana API")
    return []

def filter_preliminary(tokens):
    now = datetime.now(timezone.utc)
    filtered = []
    for token in tokens:
        if not isinstance(token, dict): continue
        raw_liq_data = token.get("liquidity", {})
        raw_liq_val = raw_liq_data.get("usd") if isinstance(raw_liq_data, dict) else token.get("liquidity")
        try: liquidity = float(raw_liq_val if raw_liq_val is not None else 0)
        except (ValueError, TypeError): liquidity = 0.0
        try: price_usd = float(token.get("priceUsd", 0))
        except (ValueError, TypeError): price_usd = 0.0
        grad_str = token.get("graduatedAt")
        minutes_diff = float('inf')
        if grad_str:
            try:
                grad = dateparser.parse(grad_str)
                if isinstance(grad, datetime.datetime):
                    grad = grad.replace(tzinfo=timezone.utc) if grad.tzinfo is None else grad.astimezone(timezone.utc)
                    minutes_diff = (now - grad).total_seconds() / 60
            except Exception as e: logging.debug(f"GraduatedAt parse error for {token.get('tokenAddress')}: {e}")
        if (liquidity >= PRELIM_LIQUIDITY_THRESHOLD and
            PRELIM_MIN_PRICE_USD <= price_usd <= PRELIM_MAX_PRICE_USD and
            minutes_diff <= PRELIM_AGE_DELTA_MINUTES):
            filtered.append(token)
    logging.info(f"{len(filtered)} tokens passed preliminary filters.")
    return filtered

async def fetch_token_data(session, token_address):
    url = f"https://api.dexscreener.com/tokens/v1/{DEXSCREENER_CHAIN_ID}/{token_address}"
    try:
        async with session.get(url, timeout=10) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("pairs") if isinstance(data, dict) else data if isinstance(data, list) else None
    except Exception:
        return None

async def fetch_all_token_data(token_addresses):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_token_data(session, addr) for addr in token_addresses]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    data_map = {}
    for addr, res in zip(token_addresses, results):
        data_map[addr] = res if not isinstance(res, Exception) else None
    return data_map

def get_token_metrics(token_pair_data_list):
    if not token_pair_data_list or not isinstance(token_pair_data_list, list): return 0.0, 0.0, 0.0
    token_data = token_pair_data_list[0] if token_pair_data_list else {}
    if not isinstance(token_data, dict): return 0.0, 0.0, 0.0
    try: price = float(token_data.get('priceUsd', token_data.get('price', 0)))
    except: price = 0.0
    liq_raw = token_data.get('liquidity', {})
    liq_val = liq_raw.get('usd', 0) if isinstance(liq_raw, dict) else liq_raw
    try: liquidity = float(liq_val if liq_val is not None else 0)
    except: liquidity = 0.0
    vol_data = token_data.get('volume', {})
    try: volume = float(vol_data.get('m5', 0) if isinstance(vol_data, dict) else 0)
    except: volume = 0.0
    return price, liquidity, volume

def whale_trap_avoidance(token_address, first_snap, second_snap):
    """
    Check if a token shows signs of a whale trap.
    
    A whale trap is detected when:
    1. Price increases significantly (WHALE_PRICE_UP_PCT)
    2. Liquidity increases significantly (WHALE_LIQUIDITY_UP_PCT)
    3. Volume doesn't increase proportionally (volume_change_pct < WHALE_VOLUME_DOWN_PCT)
    
    Returns:
        bool: True if whale trap is detected, False otherwise
    """
    price1, liquidity1, volume1 = first_snap
    price2, liquidity2, volume2 = second_snap
    
    # Calculate percentage changes
    price_change_pct = (price2 - price1) / price1 if price1 > 0 else 0
    volume_change_pct = (volume2 - volume1) / volume1 if volume1 > 0 else 0
    liq_change_pct = (liquidity2 - liquidity1) / liquidity1 if liquidity1 > 0 else 0
    
    # Log the metrics for debugging
    logging.debug(f"Whale trap check for {token_address}:")
    logging.debug(f"  • Price: ${price1:.8f} -> ${price2:.8f} ({price_change_pct*100:+.2f}%)")
    logging.debug(f"  • Liquidity: ${liquidity1:,.2f} -> ${liquidity2:,.2f} ({liq_change_pct*100:+.2f}%)")
    logging.debug(f"  • Volume: ${volume1:,.2f} -> ${volume2:,.2f} ({volume_change_pct*100:+.2f}%)")
    
    # Check for whale trap conditions
    is_whale_trap = (price_change_pct > WHALE_PRICE_UP_PCT and 
                    liq_change_pct > WHALE_LIQUIDITY_UP_PCT and 
                    volume_change_pct < WHALE_VOLUME_DOWN_PCT)
    
    if is_whale_trap:
        logging.warning(f"🐳 WHALE TRAP DETECTED: {token_address}")
        logging.warning(f"   • Price ↑: {price_change_pct*100:+.2f}% (threshold: >{WHALE_PRICE_UP_PCT*100:.2f}%)")
        logging.warning(f"   • Liquidity ↑: {liq_change_pct*100:+.2f}% (threshold: >{WHALE_LIQUIDITY_UP_PCT*100:.2f}%)")
        logging.warning(f"   • Volume ↓: {volume_change_pct*100:+.2f}% (threshold: <{WHALE_VOLUME_DOWN_PCT*100:.2f}%)")
    
    # Return True if whale trap is detected (we want to filter these out)
    return is_whale_trap

def apply_whale_trap(tokens, first_snaps, second_snaps):
    """
    Filter out tokens that show signs of a whale trap.
    
    Args:
        tokens: List of token dictionaries to check
        first_snaps: Dictionary of first snapshots for each token
        second_snaps: Dictionary of second snapshots for each token
        
    Returns:
        list: Tokens that do not show signs of a whale trap
    """
    if not tokens:
        return []
        
    safe_tokens = []
    max_workers_for_whale_trap = min(DEFAULT_MAX_WORKERS, len(tokens)) if tokens else 1
    
    with ThreadPoolExecutor(max_workers=max_workers_for_whale_trap) as executor:
        # Create a future for each token
        future_to_token = {
            executor.submit(
                whale_trap_avoidance, 
                token.get('tokenAddress'), 
                first_snaps.get(token.get('tokenAddress'), (0, 0, 0)), 
                second_snaps.get(token.get('tokenAddress'), (0, 0, 0))
            ): token 
            for token in tokens 
            if token.get('tokenAddress')
        }
        
        # Process results as they complete
        for future in as_completed(future_to_token):
            token = future_to_token[future]
            is_whale_trap = future.result()
            
            if not is_whale_trap:
                safe_tokens.append(token)
            else:
                logging.warning(f"🚫 Token {token.get('tokenAddress')} ({token.get('name', 'Unknown')}) filtered out - Whale trap detected")
    
    logging.info(f"✅ {len(safe_tokens)} of {len(tokens)} tokens passed Whale Trap Avoidance")
    return safe_tokens

def sanitize_name(name, fallback_name=None):
    if not name or name in ('None',): return fallback_name or 'Unknown'
    name_str = str(name)
    normalized_name = unicodedata.normalize('NFKC', name_str)
    sanitized = ''.join(ch for ch in normalized_name if ch.isprintable() and unicodedata.category(ch)[0] not in ('C', 'S'))
    if not sanitized: sanitized = ''.join(ch for ch in name_str if ch.isalnum() or ch in ' _-()[]{}<>')
    return sanitized[:30].strip() or (fallback_name or 'Unknown')

def load_existing_tokens(csv_filepath):
    """Load existing token addresses from CSV file"""
    existing_tokens = set()
    if os.path.exists(csv_filepath):
        try:
            with open(csv_filepath, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('Address'):
                        existing_tokens.add(row['Address'].strip())
        except Exception as e:
            logging.error(f"Error reading existing tokens from {csv_filepath}: {e}")
    return existing_tokens

def main_token_processing_loop(script_dir_path):
    """Main processing loop for token analysis."""
    from process_lock import process_lock, should_pause_execution
    
    while True:
        try:
            # Check if we should pause execution (if another process is monitoring a token)
            if should_pause_execution():
                logging.info("⏸️  Pausing token processing - Monitoring in progress")
                time.sleep(5)  # Check every 5 seconds
                continue
                
            # Get graduated tokens
            tokens = get_graduated_tokens()
            if not tokens:
                time.sleep(5)
                continue
                
            prelim_tokens = filter_preliminary(tokens)
            if not prelim_tokens:
                time.sleep(5)
                continue
            
            # Process in both 1m and 5m windows
            for win_minutes in [1, 5]:
                try:
                    # Double-check the lock before processing
                    if should_pause_execution():
                        logging.info(f"⏸️  Pausing {win_minutes}m window processing - Monitoring in progress")
                        break
                        
                    logging.info(f"🔍 Processing {len(prelim_tokens)} tokens in {win_minutes}m window")
                    process_window(win_minutes, prelim_tokens, script_dir_path)
                    
                except Exception as e:
                    logging.error(f"Error in {win_minutes}m window processing: {e}")
                    time.sleep(5)  # Short delay on error
            
            # Wait before next iteration
            time.sleep(5)
            
        except Exception as e:
            logging.error(f"Error in main processing loop: {e}")
            time.sleep(10)  # Wait longer on error

def process_window(win_minutes: int, prelim_tokens: list, script_dir_path: str) -> dict:
    """
    Process tokens in a specific time window and detect various risk patterns.
    
    Args:
        win_minutes: Time window in minutes (1m or 5m)
        prelim_tokens: List of tokens that passed preliminary filtering
        script_dir_path: Path to the script directory for file operations
        
    Returns:
        dict: Dictionary containing lists of tokens that passed different risk checks
        with keys: 'whale', 'snipe', 'ghost'
    """
    sleep_seconds = win_minutes * 60
    abs_csv_filepath = os.path.join(script_dir_path, f"sniperx_results_{win_minutes}m.csv")
    
    # Load existing tokens to avoid duplicates
    existing_tokens = load_existing_tokens(abs_csv_filepath)
    # Create a backup of the existing tokens to track new additions in this run
    existing_tokens_at_start = set(existing_tokens)

    logging.info(f"🔍 === Analyzing {len(prelim_tokens)} tokens in {win_minutes}m window ===")
    token_addresses = [t.get('tokenAddress') for t in prelim_tokens if t.get('tokenAddress')]
    if not token_addresses: 
        logging.info("No valid token addresses to process")
        return {'whale': [], 'snipe': [], 'ghost': []}
    
    # Log the start of data collection
    logging.info(f"📊 Collecting initial metrics for {len(token_addresses)} tokens...")
    first_data = asyncio.run(fetch_all_token_data(token_addresses))
    first_snaps = {addr: get_token_metrics(first_data.get(addr)) for addr in token_addresses}
    logging.info(f"✅ Captured initial metrics for {len(first_snaps)} tokens. Waiting {sleep_seconds}s...")
    
    # Wait for the time window to elapse
    time.sleep(sleep_seconds)
    
    # Collect second set of metrics
    logging.info("🔄 Collecting second set of metrics...")
    second_data = asyncio.run(fetch_all_token_data(token_addresses))
    second_snaps = {addr: get_token_metrics(second_data.get(addr)) for addr in token_addresses}
    logging.info(f"✅ Captured second set of metrics for {len(second_snaps)} tokens")
    
    # Apply whale trap avoidance filter
    logging.info("\n🐋 === WHALE TRAP DETECTION ===")
    logging.info(f"Analyzing {len(prelim_tokens)} tokens for whale trap patterns...")
    passed_whale_trap = apply_whale_trap(prelim_tokens, first_snaps, second_snaps)
    
    # Log detailed results of whale trap filtering
    if passed_whale_trap:
        logging.info(f"✅ {len(passed_whale_trap)} tokens passed whale trap avoidance")
    else:
        logging.warning("⚠️  No tokens passed whale trap avoidance checks")
        # Early return if all tokens were filtered out
        return {'whale': [], 'snipe': [], 'ghost': []}
    
    # Initialize lists for different risk categories
    snipe_candidates, ghost_buyer_candidates = [], []
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Analyze each token for snipe and ghost buyer patterns
    logging.info("🔍 Analyzing tokens for snipe and ghost buyer patterns...")
    for token_info in passed_whale_trap:
        grad_str = token_info.get('graduatedAt')
        if not grad_str: 
            continue
            
        # Parse graduation time
        grad_dt = dateparser.parse(grad_str)
        if not isinstance(grad_dt, datetime.datetime): 
            continue
            
        # Normalize timezone
        grad_dt = grad_dt.replace(tzinfo=datetime.timezone.utc) if grad_dt.tzinfo is None else grad_dt.astimezone(datetime.timezone.utc)
        age_minutes = (now_utc - grad_dt).total_seconds() / 60
        
        # Skip tokens that are too old for snipe detection
        if age_minutes > SNIPE_GRADUATED_DELTA_MINUTES_FLOAT: 
            continue
            
        addr = token_info.get('tokenAddress')
        token_name = token_info.get('name', 'Unknown')
        
        # Get price, liquidity and volume changes
        p1, l1, v1 = first_snaps.get(addr, (0, 0, 0))
        p2, l2, v2 = second_snaps.get(addr, (0, 0, 0))
        
        # Calculate percentage changes
        liq_chg = (l2-l1)/l1 if l1 else float('inf') if l2 else 0
        vol_chg = (v2-v1)/v1 if v1 else float('inf') if v2 else 0
        prc_chg = (p2-p1)/p1 if p1 else float('inf') if p2 else 0
        
        # Log token metrics for debugging
        logging.info(f"\n📊 Token: {token_name} ({addr})")
        logging.info(f"   • 💰 Price: ${p2:.8f} ({prc_chg*100:+.2f}%)")
        logging.info(f"   • 💧 Liquidity: ${l2:,.2f} ({liq_chg*100:+.2f}%)")
        logging.info(f"   • 📈 Volume: ${v2:,.2f} ({vol_chg*100:+.2f}%)")
        logging.info(f"   • 🕒 Age: {age_minutes:.2f} minutes")
        
        # Check for snipe pattern
        is_snipe = (vol_chg > 0.01 and prc_chg > 0.01 and 
                   liq_chg >= 0.05 and liq_chg > vol_chg and liq_chg > prc_chg)
        
        if is_snipe:
            logging.warning(f"🔫 SNIPE DETECTED: {token_name} in {win_minutes}m window")
            logging.warning(f"   • 📊 Volume Δ: {vol_chg*100:+.2f}% (Min: 1.00%)")
            logging.warning(f"   • 💰 Price Δ: {prc_chg*100:+.2f}% (Min: 1.00%)")
            logging.warning(f"   • 💧 Liquidity Δ: {liq_chg*100:+.2f}% (Min: 5.00%)")
            logging.warning(f"   • 📈 Liquidity > Volume: {liq_chg > vol_chg}")
            logging.warning(f"   • 📈 Liquidity > Price: {liq_chg > prc_chg}")
            snipe_candidates.append(token_info)
        
        # Check for ghost buyer pattern
        ghost_vol_min = GHOST_VOLUME_MIN_PCT_1M if win_minutes == 1 else GHOST_VOLUME_MIN_PCT_5M
        ghost_price_max = vol_chg * GHOST_PRICE_REL_MULTIPLIER
        is_ghost = (vol_chg > ghost_vol_min and 
                   abs(prc_chg) < ghost_price_max)
        
        if is_ghost:
            logging.warning(f"👻 GHOST BUYER DETECTED: {token_name} in {win_minutes}m window")
            logging.warning(f"   • 📊 Volume Δ: {vol_chg*100:+.2f}% (Min: {ghost_vol_min*100:.2f}%)")
            logging.warning(f"   • 💰 Price Δ: {prc_chg*100:+.2f}% (Max allowed: ±{ghost_price_max*100:.2f}%)")
            logging.warning(f"   • 📈 Volume to Price Ratio: {abs(vol_chg/max(0.0001, prc_chg)):.2f}x")
            logging.warning(f"   • ⚖️  Volume > {ghost_vol_min*100:.2f}%: {vol_chg > ghost_vol_min}")
            logging.warning(f"   • ⚖️  |Price| < {ghost_price_max*100:.2f}%: {abs(prc_chg) < ghost_price_max}")
            ghost_buyer_candidates.append(token_info)
    
    # Log summary of detections with emojis and timestamps
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"\n📊 === Detection Summary for {win_minutes}m window @ {timestamp} ===")
    logging.info(f"   • 🐋 Passed Whale Trap: {len(passed_whale_trap)} tokens")
    logging.info(f"   • 🔫 Snipe Detections: {len(snipe_candidates)} tokens")
    logging.info(f"   • 👻 Ghost Buyer Detections: {len(ghost_buyer_candidates)} tokens")
    
    # Log detailed risk metrics
    if snipe_candidates or ghost_buyer_candidates:
        logging.info("\n🔍 Detailed Risk Metrics:")
        for token in snipe_candidates + ghost_buyer_candidates:
            addr = token.get('tokenAddress', 'N/A')
            name = token.get('name', 'Unknown')
            risk_type = "SNIPE" if token in snipe_candidates else "GHOST"
            logging.info(f"   • {risk_type}: {name} ({addr})")
    
    logging.info("=" * 60)  # Visual separator
    
    # Combine results, removing duplicates (snipe takes precedence over ghost buyer)
    final_results_for_csv = snipe_candidates + [t for t in ghost_buyer_candidates if t not in snipe_candidates]
    
    # Filter out tokens that were already in the file at the start
    new_tokens = [t for t in final_results_for_csv 
                 if t.get('tokenAddress') and t['tokenAddress'] not in existing_tokens_at_start]
    
    if not new_tokens:
        logging.info(f" No new tokens to add to {abs_csv_filepath}")
        return {'whale': passed_whale_trap, 'snipe': snipe_candidates, 'ghost': ghost_buyer_candidates}
    
    # Write results to CSV
    write_header = not os.path.exists(abs_csv_filepath)
    
    try:
        with open(abs_csv_filepath, 'a' if os.path.exists(abs_csv_filepath) else 'w', 
                 newline='', encoding='utf-8') as csvfile:
            
            fieldnames = ['Address','Name','Price USD',f'Liquidity({win_minutes}m)',
                        f'Volume({win_minutes}m)',f'{win_minutes}m Change',
                        'Open Chart','Snipe','Ghost Buyer']
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            
            rows_added = 0
            for t_data in new_tokens:
                addr = t_data.get('tokenAddress')
                if not addr or addr in existing_tokens:
                    continue
                    
                p1, l1, v1 = first_snaps.get(addr, (0, 0, 0))
                p2, l2, v2 = second_snaps.get(addr, (0, 0, 0))
                is_snipe = t_data in snipe_candidates
                is_ghost = t_data in ghost_buyer_candidates
                
                # Format the row data
                row_data = {
                    'Address': addr, 
                    'Name': sanitize_name(t_data.get('name'), t_data.get('symbol')),
                    'Price USD': f"{p2:.8f}", 
                    f'Liquidity({win_minutes}m)': f"{l2:,.2f}",
                    f'Volume({win_minutes}m)': f"{max(0, v2-v1):.2f}", 
                    f'{win_minutes}m Change': f"{((p2/p1-1)*100 if p1 else 0):.2f}",
                    'Open Chart': f'=HYPERLINK("https://dexscreener.com/{DEXSCREENER_CHAIN_ID}/{addr}","Open Chart")',
                    'Snipe': 'Yes' if is_snipe else '', 
                    'Ghost Buyer': 'Yes' if is_ghost else ''
                }
                
                writer.writerow(row_data)
                existing_tokens.add(addr)
                rows_added += 1
                
                # Log the added token with its risk flags
                risk_flags = []
                if is_snipe: risk_flags.append("SNIPE")
                if is_ghost: risk_flags.append("GHOST BUYER")
                risk_str = ", ".join(risk_flags) if risk_flags else "No risk flags"
                logging.info(f" Added token to CSV: {row_data['Name']} - {risk_str}")
                
        if rows_added > 0:
            logging.info(f" Successfully added {rows_added} new tokens to {abs_csv_filepath}")
        else:
            logging.info(" No new tokens were added to the CSV file")
            
    except IOError as e: 
        logging.error(f" Error writing to {abs_csv_filepath}: {e}")
    except Exception as e:
        logging.error(f" Unexpected error in process_window: {e}", exc_info=True)
    
    return {'whale': passed_whale_trap, 'snipe': snipe_candidates, 'ghost': ghost_buyer_candidates}

def initialize_all_files_once(script_dir_path):
    # --- START: First-Run Reset Logic ---
    logging.info("Checking for first run...")
    first_run_marker_file = os.path.join(script_dir_path, ".sniperx_first_run_complete")

    results_file_1m_to_reset_name = "sniperx_results_1m.csv"
    opened_tokens_file_to_reset_name = "opened_tokens.txt"
    token_risk_analysis_csv_name = "token_risk_analysis.csv"
    
    token_risk_analysis_header = [
        'Address','Name','Price USD','Liquidity(1m)','Volume(1m)','1m Change','Open Chart','Snipe','Ghost Buyer',
        'Global_Cluster_Percentage','Highest_Risk_Reason_Cluster','DexScreener_Pair_Address',
        'DexScreener_Liquidity_USD','DexScreener_Token_Price_USD','DexScreener_Token_Name',
        'LP_Percent_Supply','Cluster_Token_Amount_Est','Pool_Project_Token_Amount_Est',
        'Dump_Risk_LP_vs_Cluster_Ratio','Price_Impact_Cluster_Sell_Percent',
        'Overall_Risk_Status','Risk_Warning_Details'
    ]

    if not os.path.exists(first_run_marker_file):
        logging.info("First run detected. Resetting specified files.")
        
        # Delete sniperx_results_1m.csv
        results_file_1m_path = os.path.join(script_dir_path, results_file_1m_to_reset_name)
        try:
            if os.path.exists(results_file_1m_path):
                os.remove(results_file_1m_path)
                logging.info(f"Deleted {results_file_1m_to_reset_name} as part of first-run reset.")
        except Exception as e:
            logging.error(f"Error deleting {results_file_1m_to_reset_name} during first-run reset: {e}")

        # Delete opened_tokens.txt
        opened_tokens_file_path = os.path.join(script_dir_path, opened_tokens_file_to_reset_name)
        try:
            if os.path.exists(opened_tokens_file_path):
                os.remove(opened_tokens_file_path)
                logging.info(f"Deleted {opened_tokens_file_to_reset_name} as part of first-run reset.")
        except Exception as e:
            logging.error(f"Error deleting {opened_tokens_file_to_reset_name} during first-run reset: {e}")

        # Reset token_risk_analysis.csv to its header
        token_risk_file_path = os.path.join(script_dir_path, token_risk_analysis_csv_name)
        try:
            # Delete it first, then recreate with header.
            if os.path.exists(token_risk_file_path):
                os.remove(token_risk_file_path)
            with open(token_risk_file_path, 'w', newline='', encoding='utf-8') as f_csv:
                writer = csv.writer(f_csv)
                writer.writerow(token_risk_analysis_header)
            logging.info(f"Reset {token_risk_analysis_csv_name} to header as part of first-run reset.")
        except Exception as e:
            logging.error(f"Error resetting {token_risk_analysis_csv_name} to header during first-run reset: {e}")
        
        # Create the marker file
        try:
            with open(first_run_marker_file, 'w') as f_marker:
                f_marker.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
            logging.info(f"Created first-run marker file: {first_run_marker_file}")
        except Exception as e:
            logging.error(f"Error creating first-run marker file: {e}")
    else:
        logging.info("Not the first run, skipping specific file reset.")
    # --- END: First-Run Reset Logic ---

    # --- START: Comprehensive File Initialization ---
    logging.info("Proceeding with standard file initialization checks...")
    
    files_to_initialize = {
        results_file_1m_to_reset_name: ['Address','Name','Price USD','Liquidity(1m)','Volume(1m)','1m Change','Open Chart','Snipe','Ghost Buyer'],
        # Files below have been disabled as per user request
        # "sniperx_results_5m.csv": ['Address','Name','Price USD','Liquidity(5m)','Volume(5m)','5m Change','Open Chart','Snipe','Ghost Buyer'],
        # "sniperx_prelim_filtered.csv": ['Address','Name','Price USD','Liquidity','Volume','Age (Minutes)','Created At','Open Chart'],
        # "sniperx_whale_trap_1m.csv": ['Address','Name','Price USD','Liquidity(1m)','Volume(1m)','1m Change','Open Chart'],
        # "sniperx_whale_trap_5m.csv": ['Address','Name','Price USD','Liquidity(5m)','Volume(5m)','5m Change','Open Chart'],
        # "sniperx_ghost_buyer_1m.csv": ['Address','Name','Price USD','Liquidity(1m)','Volume(1m)','1m Change','Open Chart'],
        # "sniperx_ghost_buyer_5m.csv": ['Address','Name','Price USD','Liquidity(5m)','Volume(5m)','5m Change','Open Chart'],
        "processed_tokens.txt": [], 
        opened_tokens_file_to_reset_name: [], 
        # Bubblemaps files disabled as per user request
        # "bubblemaps_processed.txt": [],
        # "bubblemaps_failed.txt": [],
        # "bubblemaps_cluster_summary.csv": ['Token Address', 'Cluster ID', 'Holder Count', 'Token Amount', 'Percentage of Supply', 'USD Value', 'Highest Risk Reason'],
        token_risk_analysis_csv_name: token_risk_analysis_header,
    }

    sniperx_config_env_name = "sniperx_config.env"
    sniperx_config_env_content = (
        "MORALIS_API_KEY_1=\"YOUR_MORALIS_API_KEY_HERE_1 # Required, get from moralis.io\"\n"
        "MORALIS_API_KEY_2=\"YOUR_MORALIS_API_KEY_HERE_2 # Optional, additional key\"\n"
        "MORALIS_API_KEY_3=\"YOUR_MORALIS_API_KEY_HERE_3 # Optional, additional key\"\n"
        "MORALIS_API_KEY_4=\"YOUR_MORALIS_API_KEY_HERE_4 # Optional, additional key\"\n"
        "MORALIS_API_KEY_5=\"YOUR_MORALIS_API_KEY_HERE_5 # Optional, additional key\"\n"
        "EXCHANGE_NAME=\"pumpfun # e.g., pumpfun, raydium_v4, etc.\"\n"
        "SNIPE_GRADUATED_DELTA_MINUTES=\"60 # Max age of token since graduation (minutes)\"\n"
        "WHALE_TRAP_WINDOW_MINUTES=\"1,5 # Comma-separated window(s) in minutes for whale trap analysis\"\n"
        "PRELIM_LIQUIDITY_THRESHOLD=\"5000 # Minimum liquidity in USD for preliminary filter\"\n"
        "PRELIM_MIN_PRICE_USD=\"0.00001 # Minimum token price in USD for preliminary filter\"\n"
        "PRELIM_MAX_PRICE_USD=\"0.0004 # Maximum token price in USD for preliminary filter\"\n"
        "PRELIM_AGE_DELTA_MINUTES=\"120 # Max age of token for preliminary filter (minutes from creation)\"\n"
        "WHALE_PRICE_UP_PCT=\"0.0 # Price increase percentage for whale detection (e.g., 0.2 for 20%)\"\n"
        "WHALE_LIQUIDITY_UP_PCT=\"0.0 # Liquidity increase percentage for whale detection\"\n"
        "WHALE_VOLUME_DOWN_PCT=\"0.0 # Volume decrease percentage for whale detection\"\n"
        "SNIPE_LIQUIDITY_MIN_PCT_1M=\"0.1 # Min liquidity % increase over 1m for SNIPE\"\n"
        "SNIPE_LIQUIDITY_MULTIPLIER_1M=\"1.5 # Liquidity multiplier for 1m SNIPE (e.g. 1.5x current)\"\n"
        "SNIPE_LIQUIDITY_UP_PCT=\"0.30 # General liquidity up % for SNIPE category (used as default for 5m if not set)\"\n"
        "SNIPE_LIQUIDITY_MIN_PCT_5M=\"0.30 # Min liquidity % increase over 5m for SNIPE (defaults to SNIPE_LIQUIDITY_UP_PCT)\"\n"
        "SNIPE_LIQUIDITY_MULTIPLIER_5M=\"5 # Liquidity multiplier for 5m SNIPE (e.g. 5x current)\"\n"
        "GHOST_VOLUME_MIN_PCT_1M=\"0.5 # Min volume % of liquidity for GHOST (1m)\"\n"
        "GHOST_VOLUME_MIN_PCT_5M=\"0.5 # Min volume % of liquidity for GHOST (5m)\"\n"
        "GHOST_PRICE_REL_MULTIPLIER=\"2.0 # Price multiplier relative to SNIPE for GHOST (e.g. 2x SNIPE price)\"\n"
        "SLAVE_WATCHDOG_INTERVAL_SECONDS=\"300 # How often the watchdog checks the slave script (Monitoring.py)\"\n"
        "SLAVE_SCRIPT_NAME=\"Monitoring.py # Name of the slave script to monitor (not used by current watchdog)\"\n"
    )
    env_file_path = os.path.join(script_dir_path, sniperx_config_env_name)
    if not os.path.exists(env_file_path):
        try:
            with open(env_file_path, 'w', encoding='utf-8') as f_env:
                f_env.write(sniperx_config_env_content)
            logging.info(f"Created template file: {sniperx_config_env_name}")
        except Exception as e:
            logging.error(f"Failed to create template file {sniperx_config_env_name}: {e}")

    for filename, header_list_or_empty in files_to_initialize.items():
        file_path = os.path.join(script_dir_path, filename)
        if not os.path.exists(file_path): # Only create if it wasn't created/reset above
            try:
                with open(file_path, 'w', newline='', encoding='utf-8') as f_generic:
                    if header_list_or_empty:
                        writer = csv.writer(f_generic)
                        writer.writerow(header_list_or_empty)
                        logging.info(f"Created CSV file with headers: {filename}")
                    else:
                        logging.info(f"Created empty file: {filename}")
            except Exception as e:
                logging.error(f"Failed to create template file {filename}: {e}")
    # --- END: Comprehensive File Initialization ---
    logging.info("File initialisation and template check complete.")

def start_slave_watchdog(script_dir_path):
    slave_script_path = os.path.join(script_dir_path, 'run_testchrone_on_csv_change.py')
    pid_file = os.path.join(script_dir_path, 'testchrone_watchdog.pid')
    if not os.path.exists(slave_script_path):
        logging.error(f"Watchdog script '{slave_script_path}' not found.")
        return None

    # Terminate previous watchdog process if PID file exists
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as pf:
                old_pid = int(pf.read().strip())
            logging.info(f"Found previous watchdog PID {old_pid}. Attempting termination...")
            os.kill(old_pid, signal.SIGTERM)
            start_time = time.time()
            while time.time() - start_time < 5:
                try:
                    os.kill(old_pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break
            else:
                os.kill(old_pid, signal.SIGKILL)
                logging.info(f"Force killed watchdog PID {old_pid} after timeout.")
        except Exception as e:
            logging.info(f"Unable to terminate previous watchdog PID from file: {e}")
        finally:
            try:
                os.remove(pid_file)
            except FileNotFoundError:
                pass

    try:
        process = subprocess.Popen([sys.executable, slave_script_path], cwd=script_dir_path)
        with open(pid_file, 'w') as pf:
            pf.write(str(process.pid))
        logging.info(f"Watchdog script '{slave_script_path}' started with PID {process.pid}.")
        return process
    except Exception as e:
        logging.error(f"Failed to start watchdog: {e}")
        return None

def main_token_processing_loop(script_dir_path):
    tokens = get_graduated_tokens()
    prelim_filtered_tokens = filter_preliminary(tokens)
    window_results_aggregator = {}
    if prelim_filtered_tokens:
        max_workers_for_windows = DEFAULT_MAX_WORKERS # This will be 1
        
        with ThreadPoolExecutor(max_workers=max_workers_for_windows, thread_name_prefix="WindowProc") as executor:
            future_to_window = {executor.submit(process_window, wd_min, prelim_filtered_tokens, script_dir_path): wd_min for wd_min in WINDOW_MINS}
            for future in as_completed(future_to_window):
                wd_min = future_to_window[future]
                try: window_results_aggregator[wd_min] = future.result()
                except Exception as exc: logging.error(f'[ERROR] Window {wd_min}m exc: {exc}')
    else: logging.info("[INFO] No tokens passed preliminary filters for this cycle.")
    return window_results_aggregator

if __name__ == "__main__":
    print("--- SniperX V2 Starting ---")
    SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
    monitoring_process = None
    watchdog_process = None
    
    initialize_all_files_once(SCRIPT_DIRECTORY) 

    monitoring_script_path = os.path.join(SCRIPT_DIRECTORY, "Monitoring.py")
    if os.path.exists(monitoring_script_path):
        try:
            monitoring_process = subprocess.Popen([sys.executable, monitoring_script_path], cwd=SCRIPT_DIRECTORY)
            logging.info(f"Successfully started Monitoring.py (PID: {monitoring_process.pid}).")
        except Exception as e:
            logging.error(f"Failed to start Monitoring.py: {e}")
    else:
        logging.warning(f"Monitoring.py not found at {monitoring_script_path}. It will not be started.")
    
    watchdog_process = start_slave_watchdog(SCRIPT_DIRECTORY)
    if not WINDOW_MINS: 
        logging.error("No WHALE_TRAP_WINDOW_MINUTES defined. Exiting.")
        if watchdog_process: watchdog_process.terminate()
        if monitoring_process: monitoring_process.terminate()
        sys.exit(1)
        
    monitoring_lock_file_path = os.path.join(SCRIPT_DIRECTORY, "monitoring_active.lock")
    check_interval_seconds = 3  # Short pause between lock checks
    last_processed_token = None
    try:
        while True:
            # Check if we need to process a new token
            # Check if monitoring is active, check if we need to force a restart
            if os.path.exists(monitoring_lock_file_path):
                # Get current time for lock file age calculation
                current_time = time.time()
                # If monitoring has been active for too long (5 minutes), force a restart
                lock_file_age = current_time - os.path.getmtime(monitoring_lock_file_path)
                if lock_file_age > 300:  # 5 minutes
                    logging.warning(f"Monitoring has been active for too long ({lock_file_age:.0f}s). Forcing restart...")
                    try:
                        os.remove(monitoring_lock_file_path)
                        logging.info("Removed monitoring lock file to force restart.")
                    except Exception as e:
                        logging.error(f"Failed to remove monitoring lock file: {e}")
                else:
                    logging.info(
                        f"Monitoring.py is active (lock file found: {monitoring_lock_file_path}). "
                        f"SniperX V2 pausing for {check_interval_seconds} seconds..."
                    )
                    time.sleep(check_interval_seconds)
                    continue

            logging.info(f"\n--- Starting new SniperX processing cycle at {datetime.datetime.now()} ---")
            
            aggregated_results = {}
            try:
                aggregated_results = main_token_processing_loop(SCRIPT_DIRECTORY)
                if aggregated_results: 
                    logging.info(f"Cycle complete. Results for windows: {list(aggregated_results.keys())}")
                    
                    # Log detailed risk information for each token
                    for window_min, results in aggregated_results.items():
                        for category, tokens in results.items():
                            for token in tokens:
                                token_address = token.get('tokenAddress', 'N/A')
                                token_name = token.get('name', 'Unknown')
                                
                                # Log risk flags if present
                                if category == 'snipe':
                                    logging.warning(f"🔫 SNIPE DETECTED: {token_name} ({token_address}) in {window_min}m window")
                                elif category == 'ghost':
                                    logging.warning(f"👻 GHOST BUYER DETECTED: {token_name} ({token_address}) in {window_min}m window")
                                elif category == 'whale':
                                    logging.warning(f"🐳 WHALE TRAP DETECTED: {token_name} ({token_address}) in {window_min}m window")
                else: 
                    logging.info("Cycle complete. No results from window processing.")
            except Exception as e_main_loop:
                logging.error(f"Unhandled exception in main processing loop: {e_main_loop}", exc_info=True)
            
            current_time_utc = datetime.datetime.now(datetime.timezone.utc)
            if current_time_utc.minute == 0 and aggregated_results: 
                hr_ts = current_time_utc.strftime('%Y%m%d_%H00')
                hr_fn = os.path.join(SCRIPT_DIRECTORY, f"sniperx_hourly_report_{hr_ts}.csv") 
                logging.info(f"Writing hourly report to {hr_fn}")
                try:
                    with open(hr_fn, 'w', newline='', encoding='utf-8') as hr_f: 
                        w = csv.writer(hr_f)
                        w.writerow(['Window_Minutes','Category','Token_Address','Token_Name','DexScreener_URL'])
                        for wm, cats_data in aggregated_results.items():
                            for cat_name, tk_list in cats_data.items():
                                for tk_item in tk_list:
                                    addr = tk_item.get('tokenAddress','N/A')
                                    name = sanitize_name(tk_item.get('name'),tk_item.get('symbol'))
                                    url = f"https://dexscreener.com/{DEXSCREENER_CHAIN_ID}/{addr}"
                                    w.writerow([wm,cat_name,addr,name,url])
                except Exception as e_rep: 
                    logging.error(f"Hourly report error: {e_rep}")
            
            logging.info(f"Rechecking monitoring lock in {check_interval_seconds}s...")
            time.sleep(check_interval_seconds)
            
    except KeyboardInterrupt: 
        logging.info("\nKeyboardInterrupt. Shutting down SniperX V2...")
    finally:
        if monitoring_process and monitoring_process.poll() is None:
            logging.info("Terminating Monitoring.py process...")
            monitoring_process.terminate()
            try:
                monitoring_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                monitoring_process.kill()
                logging.info("Monitoring.py process killed after timeout due to no response.")
            except Exception as e_mon_term:
                logging.error(f"Error during Monitoring.py termination: {e_mon_term}")

        if watchdog_process and watchdog_process.poll() is None:
            logging.info("Terminating watchdog process...")
            watchdog_process.terminate()
            pid_file = os.path.join(SCRIPT_DIRECTORY, 'testchrone_watchdog.pid')
            try:
                watchdog_process.wait(timeout=5)
                logging.info(f"Watchdog process PID {watchdog_process.pid} terminated.")
            except subprocess.TimeoutExpired:
                watchdog_process.kill()
                logging.info(f"Watchdog process PID {watchdog_process.pid} killed after timeout.")
            finally:
                if os.path.exists(pid_file):
                    os.remove(pid_file)
        logging.info("--- SniperX V2 Finished ---")