#!/usr/bin/env python3
"""
Bubblemaps Extractor - Multi-Threaded (Multiple Windows)
"""
import sys
import pytest
pytest.importorskip("selenium")
import csv
import os
import re
import logging
import time
import subprocess
from pathlib import Path
import concurrent.futures
import threading
import random
import hashlib
from collections import OrderedDict
from db_logger import log_to_db

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException,
    NoSuchWindowException
)

# --- Configuration ---
CSV_FILE = 'sniperx_results_1m.csv'
OPENED_TOKENS_FILE = 'opened_tokens.txt'
EXTRACTED_DATA_DIR = 'bubblemaps_token_data'
CLUSTER_SUMMARY_FILE = 'cluster_summaries.csv'
CSV_CURSOR_FILE = 'sniperx_csv_cursor.txt'
BMAP_SCREENSHOT_DIR = 'bubblemaps_screens'

# XPATH used to detect the presence of a cluster icon inside an address row.
# If an element matching this XPATH exists within the MuiBox container then the
# row is treated as part of a cluster.  The exact CSS class used by Bubblemaps
# changes frequently, so relying on stable DOM features like icons is more
# reliable than hard coded class names.
CLUSTER_ICON_XPATH = ".//svg[contains(@data-testid, 'Cluster') or contains(@aria-label, 'cluster') or contains(@class, 'cluster')]"
RANK1_AS_CLUSTER_KEY = "Rank1_Treated_As_Cluster" # Special key for Rank #1 if it's individual but treated as cluster

CHECK_INTERVAL = 15
CHROME_DRIVER_PATH = None
# Path to Chrome binary used if no other path is provided
DEFAULT_CHROME_BINARY_PATH = (
    r'C:\Users\Rachid Aitali\AppData\Roaming\Microsoft\Windows\Start '
    r'Menu\Programs\Chromium\chrome.exe'
)
MAX_WORKERS = 1
MAX_BUBBLEMAPS_RETRIES = 3

PROCESSED_TOKENS_LOCK = threading.Lock()
CLUSTER_SUMMARY_LOCK = threading.Lock()
CSV_CURSOR_LOCK = threading.Lock()

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(threadName)s] %(module)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler("bubblemaps_extractor_threaded.log", mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
setup_logging()

def detect_chrome_binary_path(provided_path: str | None) -> str | None:
    paths_to_check = []
    if provided_path and os.path.exists(provided_path) and os.path.isfile(provided_path):
        paths_to_check.append(provided_path)
    paths_to_check.extend([
        DEFAULT_CHROME_BINARY_PATH,
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        '/usr/bin/google-chrome-stable',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    ])
    for path_str in paths_to_check:
        path_obj = Path(path_str)
        if path_obj.exists() and path_obj.is_file():
            logging.info(f"Chrome binary found at: {path_str}")
            return str(path_obj)
    logging.warning("Could not automatically detect Chrome binary path from common locations.")
    return None

def load_processed_tokens_threadsafe(filepath: str) -> set:
    with PROCESSED_TOKENS_LOCK:
        if not os.path.exists(filepath): return set()
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logging.error(f"Error loading processed tokens from {filepath}: {e}")
            return set()

def save_processed_token_threadsafe(filepath: str, token_address: str):
    try:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(token_address + '\n')
    except Exception as e:
        logging.error(f"Error saving processed token {token_address} to {filepath}: {e}")

def load_csv_cursor(filepath: str) -> int:
    with CSV_CURSOR_LOCK:
        if not os.path.exists(filepath):
            return 0
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                val = f.read().strip()
                return int(val) if val else 0
        except Exception as e:
            logging.error(f"Error reading CSV cursor from {filepath}: {e}")
            return 0

def save_csv_cursor(filepath: str, position: int):
    with CSV_CURSOR_LOCK:
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(str(position))
        except Exception as e:
            logging.error(f"Error saving CSV cursor to {filepath}: {e}")

def get_new_tokens_from_csv_threadsafe(csv_filepath: str, current_processed_tokens: set) -> list:
    new_tokens: list[str] = []
    if not os.path.exists(csv_filepath):
        logging.warning(f"Monitored CSV file {csv_filepath} not found.")
        return new_tokens

    try:
        with open(csv_filepath, "r", newline="", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return new_tokens

        header_offset = 1 if lines[0].strip().lower().startswith("address") else 0
        total_lines = len(lines)

        cursor = load_csv_cursor(CSV_CURSOR_FILE)
        if cursor < header_offset or cursor > total_lines:
            cursor = header_offset

        processed_in_batch = set()
        for idx in range(cursor, total_lines):
            line = lines[idx].strip()
            if not line:
                continue

            token_address = line.split(',')[0].strip() if ',' in line else line

            if token_address in processed_in_batch:
                continue
            processed_in_batch.add(token_address)

            if token_address and token_address not in current_processed_tokens:
                new_tokens.append(token_address)
                logging.info(f"Found new token to process: {token_address}")
                cursor = idx + 1
                if len(new_tokens) >= 10:
                    break

        save_csv_cursor(CSV_CURSOR_FILE, cursor)
    except Exception as e:
        logging.error(f"Error reading CSV file {csv_filepath}: {e}", exc_info=True)

    logging.info(f"Found {len(new_tokens)} new tokens to process")
    return new_tokens

def initialize_driver(chrome_binary_path: str, driver_path: str | None = None) -> webdriver.Chrome | None:
    chrome_options = Options()
    if not chrome_binary_path:
        logging.error("Chrome binary path is not configured."); return None
    chrome_options.binary_location = chrome_binary_path
    chrome_options.add_argument("--start-maximized")
    try:
        service_args = {}
        if driver_path and os.path.exists(driver_path):
            service_args['executable_path'] = driver_path
        service = Service(**service_args)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logging.debug(f"[{threading.get_ident()}] WebDriver initialized.")
        return driver
    except WebDriverException as e: logging.error(f"WebDriverException on init: {e}"); return None
    except Exception as e: logging.error(f"Unexpected error on WebDriver init: {e}"); return None

def click_element_with_fallback(driver, element, timeout: int = 10, max_attempts: int = 2, log_prefix: str = "") -> bool:
    """Attempt to click an element, falling back to JS if needed."""
    for attempt in range(1, max_attempts + 1):
        try:
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(element))
            element.click()
            return True
        except Exception as click_exc:
            logging.debug(f"{log_prefix} standard click failed on attempt {attempt}: {click_exc}")
            try:
                driver.execute_script("arguments[0].click();", element)
                logging.debug(f"{log_prefix} JS click succeeded on attempt {attempt}")
                return True
            except Exception as js_exc:
                logging.debug(f"{log_prefix} JS click failed on attempt {attempt}: {js_exc}")
        time.sleep(0.5)
    logging.error(f"{log_prefix} Failed to click element after {max_attempts} attempts.")
    return False

def capture_screenshot(driver, token_addr: str, context: str) -> None:
    """Save a screenshot for debugging when a step fails."""
    os.makedirs(BMAP_SCREENSHOT_DIR, exist_ok=True)
    ts = int(time.time())
    fname = f"{token_addr}_{context}_{ts}.png"
    path = os.path.join(BMAP_SCREENSHOT_DIR, fname)
    try:
        driver.save_screenshot(path)
        logging.info(f"[{threading.get_ident()}] Screenshot saved to {path}")
    except Exception as e:
        logging.error(f"[{threading.get_ident()}] Failed to save screenshot {path}: {e}")

def ensure_address_list_panel_open(driver):
    """Ensure the Address List panel is expanded."""
    try:
        header_btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]/ancestor::button"))
        )
        collapse_div = header_btn.find_element(By.XPATH, "following-sibling::div")
        collapsed = (
            'MuiCollapse-hidden' in collapse_div.get_attribute('class') or
            collapse_div.size.get('height', 0) == 0
        )
        if collapsed:
            header_btn.click()
            time.sleep(1)
    except Exception as e:
        logging.debug(f"[{threading.get_ident()}] ensure_address_list_panel_open error: {e}")

def extract_initial_address_list_data(driver) -> list:
    TARGET_MAX_RANK_EXTRACTION = 10
    logging.debug(f"[{threading.get_ident()}] Attempting to collect the first {TARGET_MAX_RANK_EXTRACTION} ranks.")
    address_data_list = []
    try:
        WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
        )
        ensure_address_list_panel_open(driver)
        scroller = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-testid='virtuoso-scroller']"))
        )
        driver.execute_script("arguments[0].scrollTop = 0", scroller)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='virtuoso-item-list']/div[@data-item-index='0']")))
        time.sleep(2)

        items_in_view = driver.find_elements(By.XPATH, "//div[@data-testid='virtuoso-item-list']/div[@data-item-index]")
        if not items_in_view: logging.warning(f"[{threading.get_ident()}] No items in list after scroll to top."); return []

        for item_container in items_in_view:
            if len(address_data_list) >= TARGET_MAX_RANK_EXTRACTION:
                logging.debug(f"[{threading.get_ident()}] Collected {TARGET_MAX_RANK_EXTRACTION} items; stopping.")
                break
            try:
                btn = item_container.find_element(By.XPATH, ".//div[contains(@class, 'MuiListItemButton-root')]")

                # The Bubblemaps site frequently changes its CSS classes. Instead
                # of relying on those volatile class names, locate elements using
                # stable patterns in the DOM structure. The rank always starts
                # with a '#' character, the address has an 'aria-label' attribute
                # and the percentage contains a '%' sign.
                rank_el = btn.find_element(By.XPATH, ".//span[starts-with(normalize-space(), '#')]")
                addr_el = btn.find_element(By.XPATH, ".//p[@aria-label]")
                perc_el = btn.find_element(By.XPATH, ".//span[contains(text(), '%')]")
                mui_box = None
                try: mui_box = btn.find_element(By.XPATH, "./div[contains(@class, 'MuiBox-root') and not(contains(@class, 'MuiCircularProgress-root'))][1]")
                except NoSuchElementException:
                    mui_box = addr_el.find_element(By.XPATH, "./preceding-sibling::div[contains(@class, 'MuiBox-root') and not(contains(@class, 'MuiCircularProgress-root'))][1]")
                mui_class = mui_box.get_attribute("class") if mui_box else "MuiBox-Not-Found"
                has_cluster_icon = False
                if mui_box:
                    try:
                        mui_box.find_element(By.XPATH, CLUSTER_ICON_XPATH)
                        has_cluster_icon = True
                    except NoSuchElementException:
                        has_cluster_icon = False
                rank_txt, addr_txt = rank_el.text.strip().replace("#",""), addr_el.text.strip()
                if not rank_txt.isdigit():
                    continue
                if len(address_data_list) < TARGET_MAX_RANK_EXTRACTION:
                    perc_txt = perc_el.text.strip().replace("%", "")
                    address_data_list.append({
                        'Rank': rank_txt,
                        'Address': addr_txt,
                        'Individual_Percentage': perc_txt,
                        'MuiBox_Class_String': mui_class,
                        'Is_Individual_Wallet_Visual': not has_cluster_icon,
                        'Cluster_Supply_Percentage': 'N/A'
                    })
            except (StaleElementReferenceException, NoSuchElementException): logging.debug(f"[{threading.get_ident()}] Stale/missing sub-element in item."); continue
            except Exception as e: logging.error(f"[{threading.get_ident()}] Error extracting from item: {e}")
        logging.debug(f"[{threading.get_ident()}] Extracted {len(address_data_list)} for the first {TARGET_MAX_RANK_EXTRACTION} ranks.")
        return address_data_list
    except Exception as e: logging.error(f"[{threading.get_ident()}] Err in extract_initial_address_list_data: {e}", exc_info=True); return []

# --- MODIFIED FUNCTION ---
def is_dex_supply(list_item):
    """Check if the list item is a DEX supply entry"""
    try:
        # Look for Raydium or other DEX indicators in the address
        address_el = list_item.find_element(By.XPATH, ".//p[contains(@class, 'MuiTypography-body1')]")
        address_text = address_el.text.lower()
        is_dex = 'raydium:' in address_text or 'dex' in address_text or 'vault' in address_text
        return is_dex, address_el.text if is_dex else None
    except Exception as e:
        return False, None

def is_individual_cluster(list_item):
    """Check if the list item is an individual cluster"""
    try:
        # Individual clusters have a specific empty box element
        list_item.find_element(By.XPATH, ".//div[contains(@class, 'MuiBox-root') and not(*)]")
        return True
    except NoSuchElementException:
        return False

def click_clusters_and_extract_supply_data(driver, initial_data_list: list) -> tuple[list, dict]:
    thread_id_str = f"Thread-{threading.get_ident()}"
    logging.info(f"[{thread_id_str}] Starting cluster click processing for {len(initial_data_list)} items.")
    
    # Function to highlight an element (for debugging)
    def highlight_element(element, color="red", border=2):
        try:
            driver.execute_script(
                "arguments[0].style.border='{}px solid {}'".format(border, color),
                element
            )
            time.sleep(0.2)
            return True
        except:
            return False

    if not isinstance(initial_data_list, list):
        logging.error(f"[{thread_id_str}] initial_data_list is not a list. Received type: {type(initial_data_list)}")
        return [], {}

    try:
        # Wait for the address list to be visible
        WebDriverWait(driver, 15).until(
            EC.visibility_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
        )
        ensure_address_list_panel_open(driver)
        
        # Get the scroller element
        scroller = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-testid='virtuoso-scroller']"))
        )
        driver.execute_script("arguments[0].scrollTop = 0", scroller)
        time.sleep(1)  # Wait for scroll to complete
    except Exception as e:
        logging.error(f"[{thread_id_str}] Failed to initialize list view: {str(e)}")
        return initial_data_list, {}

    processed_cluster_data = {}
    augmented_data = initial_data_list.copy()

    for idx, item in enumerate(augmented_data):
        if not isinstance(item, dict) or 'Rank' not in item or 'Address' not in item:
            continue

        rank = item.get('Rank')
        address = item.get('Address')
        logging.info(f"[{thread_id_str}] Processing rank {rank} - {address}")

        try:
            # Find the list item for this address
            list_item = None
            max_scroll_attempts = 5
            found = False

            for scroll_attempt in range(max_scroll_attempts):
                try:
                    # Find all list items
                    list_items = driver.find_elements(
                        By.XPATH, 
                        "//div[contains(@class, 'MuiListItemButton-root') and .//span[contains(text(), '#')]]"
                    )
                    logging.info(f"[{thread_id_str}] Found {len(list_items)} list items on attempt {scroll_attempt + 1}")

                    for li in list_items:
                        try:
                            item_rank = li.find_element(
                                By.XPATH, 
                                ".//span[starts-with(normalize-space(), '#')]"
                            ).text.strip('#').strip()
                            item_address = li.find_element(
                                By.XPATH, 
                                ".//p[contains(@class, 'MuiTypography-root')]"
                            ).text.strip()
                            
                            if item_rank == rank and item_address == address:
                                list_item = li
                                found = True
                                logging.info(f"[{thread_id_str}] Found matching item for rank {rank}")
                                break
                        except Exception as e:
                            logging.debug(f"[{thread_id_str}] Error processing list item: {str(e)}")
                            continue
                    
                    if found:
                        break
                        
                    # If not found, scroll down
                    driver.execute_script("arguments[0].scrollTop += 300", scroller)
                    time.sleep(0.8)  # Increased wait time for scroll
                    
                except Exception as e:
                    logging.warning(f"[{thread_id_str}] Error during scroll attempt {scroll_attempt + 1}: {str(e)}")
                    time.sleep(1)

            if not list_item:
                logging.warning(f"[{thread_id_str}] Could not find list item for rank {rank} after {max_scroll_attempts} attempts")
                continue

            # Check the type of list item
            try:
                # Handle DEX supply entries
                is_dex, dex_address = is_dex_supply(list_item)
                if is_dex:
                    logging.info(f"[{thread_id_str}] Rank {rank} is a DEX supply entry")
                    item['Address_Type'] = 'DEX_SUPPLY'
                    item['DEX_Address'] = dex_address
                    # Try to get the percentage if available
                    try:
                        percent_el = list_item.find_element(By.XPATH, ".//span[contains(@class, 'MuiTypography-body2')]")
                        item['DEX_Percentage'] = percent_el.text
                    except:
                        item['DEX_Percentage'] = 'N/A'
                    continue
                
                # Handle individual clusters
                if is_individual_cluster(list_item):
                    logging.info(f"[{thread_id_str}] Rank {rank} is an individual cluster")
                    item['Address_Type'] = 'INDIVIDUAL_CLUSTER'
                    # Try to get address and percentage
                    try:
                        address_el = list_item.find_element(By.XPATH, ".//p[contains(@class, 'MuiTypography-body1')]")
                        item['Cluster_Address'] = address_el.text
                        
                        percent_el = list_item.find_element(By.XPATH, ".//span[contains(@class, 'MuiTypography-body2')]")
                        item['Cluster_Percentage'] = percent_el.text
                        
                        # Also log the rank
                        rank_el = list_item.find_element(By.XPATH, ".//span[contains(@class, 'MuiTypography-body2') and contains(@class, 'MuiListItemText-primary')]")
                        item['Cluster_Rank'] = rank_el.text.strip('# ')
                    except Exception as e:
                        logging.error(f"[{thread_id_str}] Error getting individual cluster data: {str(e)}")
                        item['Cluster_Error'] = str(e)
                    continue
                
                # If we get here, it should be a linked cluster
                logging.info(f"[{thread_id_str}] Rank {rank} appears to be a linked cluster, processing...")
                item['Address_Type'] = 'LINKED_CLUSTER'
                
                # Try to find the cluster icon using multiple strategies
                cluster_icon = None
                icon_xpaths = [
                    ".//*[local-name()='svg' and .//*[contains(@d, 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z')]]",
                    ".//*[contains(@class, 'MuiSvgIcon-root')]",
                    ".//*[contains(@class, 'cluster-icon')]"
                ]
                
                for xpath in icon_xpaths:
                    try:
                        cluster_icon = list_item.find_element(By.XPATH, xpath)
                        if cluster_icon:
                            highlight_element(cluster_icon, "green")
                            logging.info(f"[{thread_id_str}] Found cluster icon using XPath: {xpath}")
                            break
                    except:
                        continue
                
                if not cluster_icon:
                    logging.info(f"[{thread_id_str}] No cluster icon found for rank {rank}, but treating as linked cluster")
                    
            except Exception as e:
                logging.error(f"[{thread_id_str}] Error determining list item type: {str(e)}")
                item['Cluster_Supply_Percentage'] = 'ERROR'
                continue

            # Click the list item to open cluster details
            try:
                # Scroll to the item
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'}});", list_item)
                time.sleep(0.8)
                
                # Highlight before clicking
                highlight_element(list_item, "blue", 3)
                
                # Try clicking with JavaScript first
                try:
                    driver.execute_script("arguments[0].click();", list_item)
                    logging.info(f"[{thread_id_str}] Clicked on cluster using JavaScript for rank {rank}")
                except:
                    # Fall back to regular click
                    list_item.click()
                    logging.info(f"[{thread_id_str}] Clicked on cluster using Selenium for rank {rank}")
                
                # Wait for cluster details to appear
                try:
                    supply_element = WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located((By.XPATH, "//*[contains(text(), 'Cluster Supply:')]"))
                    )
                    highlight_element(supply_element, "green")
                    
                    supply_text = supply_element.text
                    supply_match = re.search(r'Cluster Supply:\s*([\d.]+)%', supply_text)
                    
                    if supply_match:
                        supply_percent = supply_match.group(1)
                        item['Cluster_Supply_Percentage'] = supply_percent
                        logging.info(f"[{thread_id_str}] Extracted cluster supply: {supply_percent}%")
                    else:
                        item['Cluster_Supply_Percentage'] = 'N/A'
                        logging.warning(f"[{thread_id_str}] Could not parse cluster supply from: {supply_text}")

                except TimeoutException:
                    logging.warning(f"[{thread_id_str}] Timed out waiting for cluster details for rank {rank}")
                    item['Cluster_Supply_Percentage'] = 'N/A'
                except Exception as e:
                    logging.error(f"[{thread_id_str}] Error extracting cluster supply: {str(e)}")
                    item['Cluster_Supply_Percentage'] = 'ERROR'

                # Close the cluster details
                try:
                    close_btn = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, 'close')]"))
                    )
                    highlight_element(close_btn, "red")
                    close_btn.click()
                    logging.info(f"[{thread_id_str}] Closed cluster details")
                except:
                    try:
                        # Try clicking outside if no close button
                        body = driver.find_element(By.TAG_NAME, 'body')
                        body.click()
                        logging.info(f"[{thread_id_str}] Clicked outside to close cluster details")
                    except:
                        logging.warning(f"[{thread_id_str}] Could not close cluster details")
                
            except Exception as e:
                logging.error(f"[{thread_id_str}] Error interacting with cluster for rank {rank}: {str(e)}")
                item['Cluster_Supply_Percentage'] = 'CLICK_ERROR'

        except Exception as e:
            logging.error(f"[{thread_id_str}] Unexpected error processing rank {rank}: {str(e)}")
            item['Cluster_Supply_Percentage'] = 'ERROR'
            continue

        # Small delay between processing items
        time.sleep(1)

    return augmented_data, processed_cluster_data

    return augmented_initial_data, processed_cluster_data

# --- END MODIFIED FUNCTION ---

def save_address_data_txt(token_address, augmented_address_data, directory):
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, f"bubblemaps_data_{token_address}.txt")
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("Rank\tAddress\tIndividual_Percentage\tIs_Individual_Wallet\tMuiBox_Class\tCluster_Supply_Percentage\n")
            for entry in augmented_address_data:
                f.write(
                    f"{entry.get('Rank','N/A')}\t{entry.get('Address','N/A')}\t{entry.get('Individual_Percentage','N/A')}\t"
                    f"{entry.get('Is_Individual_Wallet_Visual','N/A')}\t{entry.get('MuiBox_Class_String','N/A')}\t{entry.get('Cluster_Supply_Percentage','N/A')}\n"
                )
    except IOError as e: logging.error(f"IOError saving to {filepath}: {e}")

def cleanup_token_data(token_address: str):
    """Remove the saved bubblemaps text file for a token if it exists."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(script_dir, EXTRACTED_DATA_DIR)
    filepath = os.path.join(target_dir, f"bubblemaps_data_{token_address}.txt")
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"Deleted token data file {filepath}")
        else:
            logging.debug(f"Token data file not found for cleanup: {filepath}")
    except Exception as e:
        logging.error(f"Error deleting token data file {filepath}: {e}")

# --- MODIFIED FUNCTION ---
def save_cluster_summary_data(token_address: str, processed_cluster_data: dict): # Renamed arg
    # This function must be called with CLUSTER_SUMMARY_LOCK acquired

    if not processed_cluster_data:
        num_distinct_clusters = 0
        global_cluster_percentage_sum_str = "0.00"
        individual_cluster_percentages_str = "N/A"
        token_status_eval = "CLEAN"
        status_color_eval = "GREEN"
        
        # Initialize DEX and rank data structures
        dex_data = []
        rank_data = []
    else:
        num_distinct_clusters = len(processed_cluster_data)
        global_sum_float = 0.0
        valid_percentages_list = []
        dex_data = []
        rank_data = []

        for cluster_key, entry in processed_cluster_data.items():
            try:
                # Handle DEX supply entries
                if isinstance(entry, dict) and entry.get('Address_Type') == 'DEX_SUPPLY':
                    dex_info = {
                        'address': entry.get('DEX_Address', 'Unknown'),
                        'percentage': entry.get('DEX_Percentage', '0%')
                    }
                    dex_data.append(dex_info)
                    continue
                    
                # Handle individual clusters (including rank data)
                if isinstance(entry, dict) and entry.get('Address_Type') == 'INDIVIDUAL_CLUSTER':
                    rank_info = {
                        'rank': entry.get('Cluster_Rank', 'N/A'),
                        'address': entry.get('Cluster_Address', 'N/A'),
                        'percentage': entry.get('Cluster_Percentage', '0%')
                    }
                    rank_data.append(rank_info)
                
                # Extract percentage for global sum
                perc_str_val = entry
                if isinstance(entry, dict) and 'Cluster_Supply_Percentage' in entry:
                    perc_str_val = entry['Cluster_Supply_Percentage']
                
                if isinstance(perc_str_val, str) and not any(err_indicator in perc_str_val for err_indicator in ["N/A", "Error", "Pending", "Found"]):
                    try:
                        current_perc_float = float(perc_str_val.rstrip('%'))
                        global_sum_float += current_perc_float
                        
                        # For "Individual_Cluster_Percentages", show the type of cluster
                        if isinstance(entry, dict) and entry.get('Address_Type') == 'LINKED_CLUSTER':
                            addr = entry.get('Address', 'Unknown')
                            valid_percentages_list.append(f"{addr}: {current_perc_float:.2f}%")
                    except ValueError:
                        logging.warning(f"[{threading.get_ident()}] ValueError converting cluster supply '{perc_str_val}' for {token_address}, key '{cluster_key}'.")
                        
            except Exception as e:
                logging.warning(f"[{threading.get_ident()}] Error processing cluster data: {str(e)}")

        # Format the global and individual percentages
        global_cluster_percentage_sum_str = f"{global_sum_float:.2f}%"
        
        # Add DEX info to the percentages string
        for dex in dex_data:
            valid_percentages_list.append(f"DEX({dex['address']}): {dex['percentage']}")
            
        # Add rank info to the percentages string
        for rank_info in rank_data:
            valid_percentages_list.append(f"Rank#{rank_info['rank']}: {rank_info['percentage']}")
            
        individual_cluster_percentages_str = " | ".join(valid_percentages_list) if valid_percentages_list else "No valid percentages"

        # Determine token status based on cluster percentages
        if global_sum_float == 0 and not valid_percentages_list:
            token_status_eval = "NO_VALID_CLUSTER_DATA"
            status_color_eval = "GREY"
        elif global_sum_float < 5.0: # Threshold for OPPORTUNITY
            token_status_eval = "OPPORTUNITY"
            status_color_eval = "ORANGE"
        else: # >= 5.0 is RISKY
            token_status_eval = "RISKY"
            status_color_eval = "RED"

    script_dir = os.path.dirname(os.path.abspath(__file__))
    summary_filepath = os.path.join(script_dir, CLUSTER_SUMMARY_FILE)

    # Ensure directory exists
    os.makedirs(os.path.dirname(summary_filepath), exist_ok=True)

    file_needs_header = not (os.path.exists(summary_filepath) and os.path.getsize(summary_filepath) > 0)
    try:
        with open(summary_filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            csv_columns = [
                'Token_Address', 'Rank', 'Address', 'Balance', 'Percentage',
                'Address_Type', 'Cluster_Supply_Percentage', 'DEX_Address', 'DEX_Percentage',
                'Cluster_Address', 'Cluster_Percentage', 'Cluster_Rank', 'Cluster_Error',
                'Status', 'Status_Color', 'Num_Unique_Clusters', 'Global_Cluster_Percentage',
                'Individual_Cluster_Percentages'
            ]
            
            if file_needs_header:
                writer.writerow(csv_columns)
                logging.info(f"Created new cluster summary file with headers at {summary_filepath}")
            
            # First, write all individual entries
            for entry in processed_cluster_data:
                row = [
                    token_address, 
                    entry.get('Rank', '?'), 
                    entry.get('Address', '?'), 
                    entry.get('Balance', '?'), 
                    entry.get('Percentage', '?'),
                    entry.get('Address_Type', '?'), 
                    entry.get('Cluster_Supply_Percentage', '?'), 
                    entry.get('DEX_Address', '?'), 
                    entry.get('DEX_Percentage', '?'),
                    entry.get('Cluster_Address', '?'), 
                    entry.get('Cluster_Percentage', '?'), 
                    entry.get('Cluster_Rank', '?'), 
                    entry.get('Cluster_Error', '?'),
                    token_status_eval,  # Status
                    status_color_eval,  # Status Color
                    num_distinct_clusters,  # Num Unique Clusters
                    global_cluster_percentage_sum_str,  # Global Cluster Percentage
                    individual_cluster_percentages_str  # Individual Cluster Percentages
                ]
                writer.writerow(row)
            
            # Then write a summary row if needed
            summary_row = [
                token_address,  # Token Address
                'SUMMARY',      # Rank
                '',             # Address
                '',             # Balance
                '',             # Percentage
                'SUMMARY',      # Address Type
                '',             # Cluster Supply Percentage
                '',             # DEX Address
                '',             # DEX Percentage
                '',             # Cluster Address
                '',             # Cluster Percentage
                '',             # Cluster Rank
                '',             # Cluster Error
                token_status_eval,  # Status
                status_color_eval,  # Status Color
                num_distinct_clusters,  # Num Unique Clusters
                global_cluster_percentage_sum_str,  # Global Cluster Percentage
                individual_cluster_percentages_str  # Individual Cluster Percentages
            ]
            writer.writerow(summary_row)
            
            f.flush()
            os.fsync(f.fileno())
            logging.info(f"Appended cluster summary for {token_address} to {summary_filepath}")
            return True
    except Exception as e:
        logging.error(f"Error writing to cluster summary file: {str(e)}")
        return False

def save_cluster_summary_to_csv(token_address, processed_cluster_data, summary_filepath, file_needs_header):
    try:
        # Calculate summary statistics
        num_distinct_clusters = len(set(entry.get('Cluster_Address', '') for entry in processed_cluster_data if entry.get('Cluster_Address') and entry.get('Cluster_Address') != '?'))
        
        # Calculate global cluster percentage sum
        cluster_percentages = []
        for entry in processed_cluster_data:
            if entry.get('Address_Type') == 'LINKED_CLUSTER' and 'Cluster_Supply_Percentage' in entry:
                try:
                    percent = float(entry['Cluster_Supply_Percentage'].rstrip('%'))
                    cluster_percentages.append(percent)
                except (ValueError, AttributeError):
                    pass
        
        global_cluster_percentage_sum = sum(cluster_percentages)
        global_cluster_percentage_sum_str = f"{global_cluster_percentage_sum:.2f}%"
        
        # Create individual cluster percentages string
        individual_percentages = []
        for entry in processed_cluster_data:
            if entry.get('Address_Type') in ['LINKED_CLUSTER', 'INDIVIDUAL_CLUSTER']:
                addr = entry.get('Address', entry.get('Cluster_Address', 'Unknown'))
                percent = entry.get('Percentage', entry.get('Cluster_Percentage', '0%'))
                individual_percentages.append(f"{addr}: {percent}")
        
        individual_cluster_percentages_str = " | ".join(individual_percentages)
        
        # Determine status based on cluster percentages
        if global_cluster_percentage_sum > 90:
            token_status_eval = "High Risk"
            status_color_eval = "red"
        elif global_cluster_percentage_sum > 70:
            token_status_eval = "Medium Risk"
            status_color_eval = "orange"
        else:
            token_status_eval = "Low Risk"
            status_color_eval = "green"
        
        with open(summary_filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            csv_columns = [
                'Token_Address', 'Rank', 'Address', 'Balance', 'Percentage',
                'Address_Type', 'Cluster_Supply_Percentage', 'DEX_Address', 'DEX_Percentage',
                'Cluster_Address', 'Cluster_Percentage', 'Cluster_Rank', 'Cluster_Error',
                'Status', 'Status_Color', 'Num_Unique_Clusters', 'Global_Cluster_Percentage',
                'Individual_Cluster_Percentages', 'Timestamp'
            ]
            
            if file_needs_header:
                writer.writerow(csv_columns)
                logging.info(f"Created new cluster summary file with headers at {summary_filepath}")
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Write all individual entries
            for entry in processed_cluster_data:
                row = [
                    token_address, 
                    entry.get('Rank', '?'), 
                    entry.get('Address', '?'), 
                    entry.get('Balance', '?'), 
                    entry.get('Percentage', '?'),
                    entry.get('Address_Type', '?'), 
                    entry.get('Cluster_Supply_Percentage', '?'), 
                    entry.get('DEX_Address', '?'), 
                    entry.get('DEX_Percentage', '?'),
                    entry.get('Cluster_Address', '?'), 
                    entry.get('Cluster_Percentage', '?'), 
                    entry.get('Cluster_Rank', '?'), 
                    entry.get('Cluster_Error', '?'),
                    token_status_eval,
                    status_color_eval,
                    num_distinct_clusters,
                    global_cluster_percentage_sum_str,
                    individual_cluster_percentages_str,
                    current_time
                ]
                writer.writerow(row)
            
            # Write a summary row
            summary_row = [
                token_address,
                'SUMMARY',
                '', '', '', 'SUMMARY',
                '', '', '', '', '', '', '',
                token_status_eval,
                status_color_eval,
                num_distinct_clusters,
                global_cluster_percentage_sum_str,
                individual_cluster_percentages_str,
                current_time
            ]
            writer.writerow(summary_row)
            
            f.flush()
            os.fsync(f.fileno())
            
            # Log to database if needed
            try:
                log_to_db('cluster_summaries', {
                    'Token_Address': token_address,
                    'Num_Unique_Clusters': num_distinct_clusters,
                    'Global_Cluster_Percentage': global_cluster_percentage_sum_str,
                    'Individual_Cluster_Percentages': individual_cluster_percentages_str,
                    'Status': token_status_eval,
                    'Status_Color': status_color_eval,
                    'Timestamp': current_time
                })
            except Exception as db_exc:
                logging.error(f"DB log error: {db_exc}")
            
            logging.info(f"Saved cluster summary for {token_address}: "
                        f"Clusters={num_distinct_clusters}, "
                        f"GlobalSupply={global_cluster_percentage_sum_str}, "
                        f"Status={token_status_eval}")
            
            return True
            
    except IOError as e:
        logging.error(f"IOError saving cluster summary: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error in save_cluster_summary_to_csv: {str(e)}", exc_info=True)
        return False

def process_single_token_threaded(token_address_with_config: tuple):
    # ... (rest of the code remains the same)
    token_address, chrome_binary_path_config, chrome_driver_path_override_config = token_address_with_config
    thread_id_str = f"Thread-{threading.get_ident()}"
    thread_driver = None
    logging.info(f"[{thread_id_str}] --- Starting processing for token: {token_address} ---")

    try: # Outer try for driver initialization and final cleanup
        thread_driver = initialize_driver(chrome_binary_path_config, chrome_driver_path_override_config)
        if not thread_driver:
            return token_address, False

        bubblemaps_url = f"https://v2.bubblemaps.io/map?address={token_address}&chain=solana&limit=100"

        for attempt in range(MAX_BUBBLEMAPS_RETRIES):
            logging.info(f"[{thread_id_str}] Attempt {attempt + 1}/{MAX_BUBBLEMAPS_RETRIES} for token: {token_address}")
            attempt_successful = False
            try: # Inner try for a single attempt's logic
                # Initial Navigation and Page Load
                if attempt > 0: # Reload page on retries
                    logging.info(f"[{thread_id_str}] Reloading page for attempt {attempt + 1}.")
                    thread_driver.refresh()
                else: # First attempt, just load
                    thread_driver.get(bubblemaps_url)

                WebDriverWait(thread_driver, 75).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                logging.info(f"[{thread_id_str}] Page readyState complete for {token_address} (Attempt {attempt + 1}).")

                # Wait for critical content 'Address List' before proceeding
                try:
                    WebDriverWait(thread_driver, 45).until(
                        EC.visibility_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
                    )
                    ensure_address_list_panel_open(thread_driver)
                    logging.info(f"[{thread_id_str}] Initial 'Address List' is visible for {token_address} (Attempt {attempt + 1}).")
                    time.sleep(random.uniform(1.5, 2.5))
                except TimeoutException:
                    logging.error(f"[{thread_id_str}] Critical: 'Address List' not visible on attempt {attempt + 1}.")
                    if attempt < MAX_BUBBLEMAPS_RETRIES - 1: continue
                    else: return token_address, False # All retries failed for this critical step

                # 1. Data Freshness Check
                data_is_fresh = False
                try:
                    timestamp_el = WebDriverWait(thread_driver, 15).until(EC.presence_of_element_located((By.XPATH, "//p[contains(@class, 'MuiTypography-root') and contains(@class, 'css-fm451k')]")))
                    ts_text = timestamp_el.text.strip().lower()
                    logging.info(f"[{thread_id_str}] Timestamp: '{ts_text}' (Attempt {attempt + 1}).")
                    if "a few seconds ago" in ts_text or "live" in ts_text:
                        data_is_fresh = True
                        logging.info(f"[{thread_id_str}] Data is fresh (Attempt {attempt + 1}).")
                except TimeoutException:
                    logging.warning(f"[{thread_id_str}] Timestamp element not found (Attempt {attempt + 1}). Assuming refresh needed.")
                except Exception as e_ts:
                    logging.warning(f"[{thread_id_str}] Error checking timestamp (Attempt {attempt + 1}): {e_ts}. Assuming refresh needed.")

                if not data_is_fresh:
                    logging.info(f"[{thread_id_str}] Data not fresh. Attempting in-page refresh (Attempt {attempt + 1}).")
                    refresh_icon_found = False
                    refresh_icon_found_and_clicked = False
                    
                    # First check if refresh icon exists
                    try:
                        refresh_icon = WebDriverWait(thread_driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, "//*[@data-testid='RefreshIcon']"))
                        )
                        refresh_icon_found = True
                    except TimeoutException:
                        logging.warning(f"[{thread_id_str}] Refresh icon not found on page (Attempt {attempt + 1}).")
                        if attempt >= 1:  # If this is the 2nd or later attempt and still no refresh icon
                            logging.error(f"[{thread_id_str}] Refresh icon not found after {attempt + 1} attempts. Adding to opened_tokens.txt for retry.")
                            try:
                                with open('opened_tokens.txt', 'a') as f:
                                    f.write(f"{token_address}\n")
                                logging.info(f"[{thread_id_str}] Added token {token_address} to opened_tokens.txt for retry.")
                            except Exception as e:
                                logging.error(f"[{thread_id_str}] Failed to add token to opened_tokens.txt: {e}")
                            return token_address, False
                        continue  # Try again on next attempt
                    except Exception as e:
                        logging.warning(f"[{thread_id_str}] Error checking for refresh icon: {e}")
                        continue
                    
                    # If we get here, refresh icon was found, now try to click it
                    try:
                        thread_driver.execute_script("arguments[0].scrollIntoView(true);", refresh_icon)
                        time.sleep(0.5)
                        click_element_with_fallback(thread_driver, refresh_icon, timeout=5, max_attempts=3, log_prefix=f"[{thread_id_str}] Refresh")
                        logging.info(f"[{thread_id_str}] Clicked in-page refresh (Attempt {attempt + 1}).")
                        refresh_icon_found_and_clicked = True
                    except Exception as e_icon_click:
                        logging.warning(f"[{thread_id_str}] Error clicking refresh icon (Attempt {attempt + 1}): {e_icon_click}")
                        if attempt >= 1:  # If this is the 2nd or later attempt and still can't click
                            logging.error(f"[{thread_id_str}] Failed to click refresh icon after {attempt + 1} attempts. Skipping token.")
                            return token_address, False
                        continue  # Try again on next attempt

                    if refresh_icon_found_and_clicked:
                        try:
                            # If icon was clicked, now wait for list and re-check freshness
                            WebDriverWait(thread_driver, 60).until(
                                EC.visibility_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
                            )
                            ensure_address_list_panel_open(thread_driver)
                            time.sleep(random.uniform(4, 6)) # Wait for list to reload
                            timestamp_el = WebDriverWait(thread_driver, 15).until(
                                EC.presence_of_element_located((By.XPATH, "//p[contains(@class, 'MuiTypography-root') and contains(@class, 'css-fm451k')]"))
                            )
                            ts_text = timestamp_el.text.strip().lower()
                            if "a few seconds ago" in ts_text or "live" in ts_text:
                                data_is_fresh = True
                                logging.info(f"[{thread_id_str}] Data fresh after successful in-page refresh (Attempt {attempt + 1}).")
                            else:
                                logging.warning(f"[{thread_id_str}] Data still not fresh after in-page refresh completed. Timestamp: '{ts_text}' (Attempt {attempt + 1}).")
                        except Exception as e_after_click:
                            logging.warning(f"[{thread_id_str}] Error after clicking in-page refresh (e.g., waiting for list or re-checking freshness) (Attempt {attempt + 1}): {e_after_click}.")
                            # data_is_fresh remains as it was before this inner try (likely False).
                    # If refresh_icon_found_and_clicked is False, data_is_fresh is also False (or its previous state),
                    # and the code will naturally proceed to the full browser refresh check if needed.

                if not data_is_fresh:
                    logging.info(f"[{thread_id_str}] Data still not fresh. Attempting full browser refresh (Attempt {attempt + 1}).")
                    try:
                        thread_driver.refresh()
                        WebDriverWait(thread_driver, 75).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                        WebDriverWait(thread_driver, 60).until(
                            EC.visibility_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
                        )
                        ensure_address_list_panel_open(thread_driver)
                        time.sleep(random.uniform(4,6))
                        # Re-check freshness again
                        timestamp_el = WebDriverWait(thread_driver, 15).until(EC.presence_of_element_located((By.XPATH, "//p[contains(@class, 'MuiTypography-root') and contains(@class, 'css-fm451k')]")))
                        ts_text = timestamp_el.text.strip().lower()
                        if "a few seconds ago" in ts_text or "live" in ts_text:
                            data_is_fresh = True
                            logging.info(f"[{thread_id_str}] Data fresh after full browser refresh (Attempt {attempt + 1}).")
                        else:
                            logging.error(f"[{thread_id_str}] Data NOT fresh after all refresh attempts (Attempt {attempt + 1}). Timestamp: '{ts_text}'.")
                    except Exception as e_full_refresh:
                        logging.error(f"[{thread_id_str}] Full browser refresh failed or data still not fresh (Attempt {attempt + 1}): {e_full_refresh}.")

                if not data_is_fresh:
                    logging.error(f"[{thread_id_str}] CRITICAL: Data not fresh after all checks and refreshes on attempt {attempt + 1}.")
                    capture_screenshot(thread_driver, token_address, "stale_data")
                    if attempt < MAX_BUBBLEMAPS_RETRIES - 1:
                        continue
                    else:
                        return token_address, False

                # 3. Initial Data Extraction & Rank 01 Check
                initial_data = extract_initial_address_list_data(thread_driver)
                if not initial_data:
                    logging.error(f"[{thread_id_str}] Failed to extract initial address list (Attempt {attempt + 1}).")
                    if attempt < MAX_BUBBLEMAPS_RETRIES - 1: continue
                    else: return token_address, False

                # Verify we have at least some data (at least 1 rank)
                if not initial_data:
                    logging.error(f"[{thread_id_str}] CRITICAL: No rank data found in initial list (Attempt {attempt + 1}).")
                    if attempt < MAX_BUBBLEMAPS_RETRIES - 1: 
                        continue
                    else: 
                        return token_address, False
                        
                # Log which ranks we found
                found_ranks = [item.get('Rank', '?') for item in initial_data]
                logging.info(f"[{thread_id_str}] Found ranks: {found_ranks} (Attempt {attempt + 1})")
                
                # If we have less than 10 items, that's fine - we'll just process what we have
                if len(initial_data) < 10:
                    logging.warning(f"[{thread_id_str}] Only found {len(initial_data)} ranks, which is less than 10 (Attempt {attempt + 1})")

                # 4. Cluster Data Extraction (Presence of clusters is optional, but function should not error)
                logging.info(f"[{thread_id_str}] Attempting to click clusters and extract supply data (Attempt {attempt + 1}).")
                aug_data, clusters_info = click_clusters_and_extract_supply_data(thread_driver, initial_data)
                # clusters_info being empty is acceptable as per user comment.
                # aug_data containing data is a good sign.
                if not aug_data and initial_data: # If we had initial data but got no augmented data, it's a bit suspicious but not a hard fail for retry unless an exception occurred.
                    logging.warning(f"[{thread_id_str}] No augmented data from click_clusters_and_extract_supply_data, but initial data existed (Attempt {attempt + 1}).")
                elif not initial_data and not aug_data:
                    logging.info(f"[{thread_id_str}] No initial or augmented data, likely an empty/new token (Attempt {attempt + 1}).")
                else:
                    logging.info(f"[{thread_id_str}] click_clusters_and_extract_supply_data completed (Attempt {attempt + 1}).")

                # If all checks passed and critical data extracted:
                save_address_data_txt(token_address, aug_data, EXTRACTED_DATA_DIR)
                if isinstance(clusters_info, dict):
                    with CLUSTER_SUMMARY_LOCK:
                        save_cluster_summary_data(token_address, clusters_info)
                else:
                    logging.warning(f"[{thread_id_str}] clusters_info was not a dict, type: {type(clusters_info)}. Skipping summary save. (Attempt {attempt + 1})")

                logging.info(f"[{thread_id_str}] Successfully processed and saved data for {token_address} on attempt {attempt + 1}.")
                attempt_successful = True
                break # Exit the retry loop as this attempt was successful

            except NoSuchWindowException as e_critical_driver_attempt:
                logging.error(f"[{thread_id_str}] CRITICAL DRIVER ERROR during attempt {attempt + 1} for {token_address}: {e_critical_driver_attempt}. Window closed or driver crashed.")
                return token_address, False # Critical, cannot recover this thread's driver
            except Exception as e_attempt:
                logging.error(f"[{thread_id_str}] Error during attempt {attempt + 1} for token {token_address}: {e_attempt}", exc_info=True)
                # This catch-all will handle unexpected errors during an attempt.
                # If it's not the last attempt, the loop will naturally continue after a delay (handled below).

            if attempt_successful:
                return token_address, True # Successful attempt, exit function

            # If this attempt was not successful and it's not the last attempt
            if not attempt_successful and attempt < MAX_BUBBLEMAPS_RETRIES - 1:
                logging.info(f"[{thread_id_str}] Attempt {attempt + 1} failed for {token_address}. Retrying after delay...")
                time.sleep(random.uniform(5, 8)) # General delay before next attempt

        # After the for loop, check the outcome
        if attempt_successful: # This flag is from the scope of the attempt that successfully broke the loop
            # The success log ("Successfully processed and saved data...") would have already been printed within the successful attempt.
            return token_address, True
        else:
            # This 'else' is reached if the loop completed all iterations without 'attempt_successful' becoming True.
            # Critical driver errors (NoSuchWindowException) would have returned False earlier and exited the function.
            logging.error(f"[{thread_id_str}] All {MAX_BUBBLEMAPS_RETRIES} attempts failed for token {token_address} after exhausting retries.")
            return token_address, False
    except NoSuchWindowException:
        logging.error(f"[{thread_id_str}] Browser window closed unexpectedly for token: {token_address}")
        # No driver to quit here as it's already gone.
        thread_driver = None # Ensure it's None so finally block doesn't try to quit again
        return token_address, False
    except WebDriverException as e_wd:
        logging.error(f"[{thread_id_str}] WebDriverException for {token_address}: {e_wd}")
        return token_address, False
    except Exception as e:
        logging.error(f"[{thread_id_str}] General error in process_single_token_threaded for {token_address}: {e}", exc_info=True)
        return token_address, False
    finally:
        if thread_driver:
            try:
                thread_driver.quit()
                logging.info(f"[{thread_id_str}] WebDriver quit for {token_address}.")
            except Exception as e_quit:
                logging.error(f"[{thread_id_str}] Error quitting WebDriver for {token_address}: {e_quit}")
    return token_address, False # Should be unreachable if try block returns, but as a safeguard
def run_risk_detector_once():
    logging.info("Attempting to run risk_detector.py...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    risk_detector_script = os.path.join(script_dir, "risk_detector.py")
    if not os.path.exists(risk_detector_script):
        logging.error(f"risk_detector.py not found at {risk_detector_script}.")
        return False
    try:
        # Check if input files exist
        cluster_summary_path = os.path.join(script_dir, CLUSTER_SUMMARY_FILE)
        if not os.path.exists(cluster_summary_path):
            logging.error(f"Cluster summary file not found at {cluster_summary_path}")
            return False

        # Run risk detector with full path to Python executable
        proc = subprocess.run(
            [sys.executable, risk_detector_script],
            check=True,
            cwd=script_dir,
            capture_output=True,
            text=True
        )
        logging.info("risk_detector.py completed successfully.")
        if proc.stdout: logging.info(f"Risk Detector STDOUT:\n{proc.stdout.strip()}")
        if proc.stderr: logging.warning(f"Risk Detector STDERR:\n{proc.stderr.strip()}")

        # Verify output file was created
        output_file = os.path.join(script_dir, "filtered_tokens_with_all_risks.csv")
        if os.path.exists(output_file):
            logging.info(f"Risk detector output file created at {output_file}")
        else:
            logging.error(f"Risk detector output file not created at {output_file}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Risk detector failed with exit code {e.returncode}")
        if e.stdout:
            logging.error(f"STDOUT: {e.stdout}")
        if e.stderr:
            logging.error(f"STDERR: {e.stderr}")
    except Exception as e:
        logging.error(f"Error running risk_detector.py: {e}", exc_info=True)
    return False

def clean_duplicate_entries(csv_path: str) -> None:
    """Remove duplicate token entries from the CSV file."""
    try:
        if not os.path.exists(csv_path):
            return
            
        # Read all lines and remove duplicates while preserving order
        with open(csv_path, 'r', newline='', encoding='utf-8') as f:
            lines = f.readlines()
            
        if not lines:
            return
            
        header = lines[0] if lines[0].strip().lower().startswith('address') else None
        seen = set()
        unique_lines = []
        
        for line in lines[1:] if header else lines:
            line = line.strip()
            if not line:
                continue
                
            # Extract token address (first column)
            token = line.split(',')[0].strip()
            if token and token not in seen:
                seen.add(token)
                unique_lines.append(line + '\n')
        
        # Only write back if we found duplicates
        if len(unique_lines) < (len(lines) - (1 if header else 0)):
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                if header:
                    f.write(header)
                f.writelines(unique_lines)
            logging.info(f"Removed {len(lines) - len(unique_lines) - (1 if header else 0)} duplicate entries from {csv_path}")
            
    except Exception as e:
        logging.error(f"Error cleaning duplicate entries from {csv_path}: {e}")

def get_file_checksum(file_path: str) -> str:
    """Calculate MD5 checksum of a file to detect changes."""
    if not os.path.exists(file_path):
        return ""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def monitor_and_process_tokens(csv_fpath: str, chrome_bin_path: str, chrome_drv_path: str | None):
    # Clean up any duplicate entries in the CSV file first
    clean_duplicate_entries(csv_fpath)
    
    processed_set = load_processed_tokens_threadsafe(OPENED_TOKENS_FILE)
    in_flight = set()
    newly_processed_count = 0
    last_checksum = get_file_checksum(csv_fpath)
    last_check_time = time.time()
    processed_since_last_run: set[str] = set()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="BubbleWorker") as executor:
            active_futures = {}
            while True:
                processed_this_cycle = False
                done_fkeys = [f for f in active_futures if f.done()]
                for fkey in done_fkeys:
                    token_addr = active_futures.pop(fkey)
                    in_flight.discard(token_addr)
                    try:
                        _, success = fkey.result()
                        if success:
                            with PROCESSED_TOKENS_LOCK:
                                save_processed_token_threadsafe(OPENED_TOKENS_FILE, token_addr)
                                processed_set.add(token_addr)
                            logging.info(f"Bubblemaps SUCCESS for {token_addr}.")
                            newly_processed_count += 1
                            processed_since_last_run.add(token_addr)
                            processed_this_cycle = True
                        else: logging.error(f"Bubblemaps FAILED for {token_addr}.")
                    except Exception as exc: logging.error(f"Token {token_addr} thread exception: {exc}")

                if newly_processed_count > 0 and not active_futures:
                    logging.info(f"{newly_processed_count} tokens updated. Triggering risk_detector.py.")
                    detector_success = run_risk_detector_once()
                    if detector_success:
                        for addr in processed_since_last_run:
                            cleanup_token_data(addr)
                        processed_since_last_run.clear()
                    newly_processed_count = 0

                # Check if file has been modified using checksum for better reliability
                current_time = time.time()
                if current_time - last_check_time >= 5:  # Check every 5 seconds
                    current_checksum = get_file_checksum(csv_fpath)
                    if current_checksum != last_checksum:
                        logging.info("CSV file content changed, checking for new tokens")
                        last_checksum = current_checksum
                        # Clean up duplicates whenever we detect a change
                        clean_duplicate_entries(csv_fpath)
                    last_check_time = current_time

                if len(active_futures) < MAX_WORKERS:
                    with PROCESSED_TOKENS_LOCK:
                        to_avoid = processed_set.union(in_flight)
                        new_tokens = get_new_tokens_from_csv_threadsafe(csv_fpath, to_avoid)
                        to_submit = []
                        for token in new_tokens:
                            if len(active_futures) + len(to_submit) < MAX_WORKERS:
                                to_submit.append(token)
                                in_flight.add(token)  # Add to in_flight before submitting
                            else:
                                break

                    if to_submit:
                        logging.info(f"Submitting {len(to_submit)} new tokens: {', '.join(to_submit)}")
                        for token_s in to_submit:
                            fut = executor.submit(process_single_token_threaded, (token_s, chrome_bin_path, chrome_drv_path))
                            active_futures[fut] = token_s
                    elif not active_futures and not processed_this_cycle and newly_processed_count == 0:
                        logging.info(f"No new tokens, no active tasks. Waiting {CHECK_INTERVAL}s...")
                        time.sleep(CHECK_INTERVAL)
                    else:
                        time.sleep(5)
                else:
                    logging.info(f"Pool full ({MAX_WORKERS}). Waiting...")
                    time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt. Shutting down...")
    except Exception as e:
        logging.critical(f"Main loop error: {e}", exc_info=True)
    finally:
        logging.info("Monitor loop finished.")

if __name__ == '__main__':
    logging.info("--- Starting Bubblemaps Extractor (Multi-Threaded) ---")
    cli_chrome = sys.argv[1] if len(sys.argv) > 1 else None
    actual_chrome = detect_chrome_binary_path(cli_chrome)
    if not actual_chrome: logging.error("Exiting: Chrome binary undetermined."); sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_monitor_fpath = os.path.join(script_dir, CSV_FILE)
    data_dir_fpath = os.path.join(script_dir, EXTRACTED_DATA_DIR)
    os.makedirs(data_dir_fpath, exist_ok=True)

    if not os.path.exists(os.path.dirname(csv_monitor_fpath)): logging.warning(f"Dir for CSV ('{os.path.dirname(csv_monitor_fpath)}') missing.")
    elif not os.path.exists(csv_monitor_fpath): logging.warning(f"CSV ('{csv_monitor_fpath}') missing.")

    monitor_and_process_tokens(csv_monitor_fpath, actual_chrome, CHROME_DRIVER_PATH)
    logging.info("--- Bubblemaps Extractor (Multi-Threaded) finished. ---")
