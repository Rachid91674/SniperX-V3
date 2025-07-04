"""
Bubblemaps Processor - Handles token processing and data extraction from bubblemaps.
"""
import csv
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bubblemaps_processor.log'),
        logging.StreamHandler()
    ]
)

# Constants
CLUSTER_SUMMARY_FILE = 'cluster_summaries.csv'
OPENED_TOKENS_FILE = 'opened_tokens.txt'

class BubblemapsProcessor:
    """Handles bubblemaps token processing and data extraction."""
    
    def __init__(self, driver: WebDriver, thread_id: str = ""):
        """Initialize the processor with a WebDriver instance."""
        self.driver = driver
        self.thread_id = thread_id or f"Thread-{os.getpid()}"
        self.log = logging.getLogger(f"{self.__class__.__name__}.{self.thread_id}")
    
    def check_refresh_status(self) -> Tuple[bool, bool]:
        """Check if the bubblemaps data is fresh or can be refreshed."""
        try:
            status_el = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(@class,'MuiTypography-root') and contains(text(),'Refreshed')]")
                )
            )
            status_text = status_el.text.lower()
            
            if 'a few seconds ago' in status_text or 'live' in status_text:
                self.log.info("Data is fresh")
                return True, False
                
            if 'refreshing' in status_text:
                self.log.info("Data is refreshing, waiting...")
                time.sleep(5)
                return False, True
                
            try:
                refresh_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//*[contains(@data-testid, 'RefreshIcon')]]"))
                )
                refresh_btn.click()
                self.log.info("Clicked refresh button")
                time.sleep(2)
                return False, True
                
            except Exception:
                self.log.warning("No refresh button found")
                return False, False
                
        except Exception as e:
            self.log.error(f"Error checking refresh status: {e}")
            return False, False

    def ensure_address_list_panel_open(self) -> bool:
        """Ensure the address list panel is open."""
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//p[contains(text(),'Address List')]"))
            )
            return True
        except Exception:
            self.log.warning("Address list panel not found")
            return False

    def extract_rank_data(self) -> List[Dict]:
        """Extract rank data from the bubblemaps page."""
        ranks = []
        try:
            if not self.ensure_address_list_panel_open():
                self.log.error("Could not open address list panel")
                return ranks
            
            rows = WebDriverWait(self.driver, 15).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "div[role='row']:not(:first-child)")
                )
            )
            self.log.info(f"Found {len(rows)} rank rows")
            
            for i, row in enumerate(rows[:10], 1):
                try:
                    rank = i
                    address = row.find_element(
                        By.CSS_SELECTOR, 
                        "span[class*='MuiTypography-root']"
                    ).text.strip()
                    
                    percentage = row.find_element(
                        By.CSS_SELECTOR, 
                        "div[role='cell']:nth-child(3)"
                    ).text.strip()
                    
                    is_cluster = bool(row.find_elements(
                        By.XPATH, 
                        ".//*[contains(@data-testid, 'Cluster') or contains(@aria-label, 'cluster')]"
                    ))
                    
                    ranks.append({
                        'rank': rank,
                        'address': address,
                        'percentage': percentage,
                        'is_cluster': is_cluster,
                        'global_cluster_percentage': ''
                    })
                    
                except Exception as e:
                    self.log.warning(f"Error processing rank {i}: {e}")
                    
        except Exception as e:
            self.log.error(f"Error extracting rank data: {e}")
        
        return ranks

    def process_cluster_data(self, ranks_data: List[Dict]) -> None:
        """Process cluster data for ranks that are clusters."""
        for rank_data in ranks_data:
            if not rank_data.get('is_cluster'):
                continue
                
            try:
                rank = rank_data['rank']
                self.log.info(f"Processing cluster for rank {rank}")
                
                row_xpath = f"//div[@role='row'][.//span[contains(text(),'\"{rank_data['address']}\"')]]"
                row = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, row_xpath))
                )
                row.click()
                time.sleep(1)
                
                try:
                    global_pct = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//*[contains(text(),'Global Cluster')]/following-sibling::div")
                        )
                    ).text.strip()
                    rank_data['global_cluster_percentage'] = global_pct
                except Exception:
                    self.log.warning("Could not find global cluster percentage")
                    
                try:
                    close_btn = self.driver.find_element(
                        By.XPATH, 
                        "//button[.//*[contains(@data-testid, 'Close')]]"
                    )
                    close_btn.click()
                    time.sleep(0.5)
                except Exception:
                    self.log.warning("Could not find close button")
                    
            except Exception as e:
                self.log.error(f"Error processing cluster for rank {rank_data.get('rank')}: {e}")

    def save_cluster_summary(self, token_address: str, ranks_data: List[Dict]) -> None:
        """Save the cluster summary data to CSV."""
        if not ranks_data:
            self.log.warning("No rank data to save")
            return
            
        rows = []
        for rank_data in ranks_data:
            rows.append({
                'token_address': token_address,
                'rank': rank_data['rank'],
                'address': rank_data['address'],
                'supply_percentage': rank_data['percentage'],
                'is_cluster': str(rank_data['is_cluster']).lower(),
                'global_cluster_percentage': rank_data.get('global_cluster_percentage', ''),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        
        file_exists = os.path.exists(CLUSTER_SUMMARY_FILE)
        try:
            with open(CLUSTER_SUMMARY_FILE, 'a', newline='', encoding='utf-8') as f:
                fieldnames = [
                    'token_address', 'rank', 'address', 'supply_percentage',
                    'is_cluster', 'global_cluster_percentage', 'timestamp'
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                for row in rows:
                    writer.writerow(row)
                    
            self.log.info(f"Saved cluster data for token {token_address}")
            
        except Exception as e:
            self.log.error(f"Error saving cluster summary: {e}")

    def process_token(self, token_address: str) -> bool:
        """Process a single token's bubblemaps page."""
        try:
            url = f"https://bubblemaps.io/token/{token_address}"
            self.log.info(f"Navigating to {url}")
            self.driver.get(url)
            
            is_fresh, should_retry = self.check_refresh_status()
            if should_retry:
                time.sleep(3)
                is_fresh, _ = self.check_refresh_status()
                
            if not is_fresh:
                self.log.warning("Data is stale and cannot be refreshed")
                return False
                
            ranks_data = self.extract_rank_data()
            if not ranks_data:
                self.log.warning("No rank data found")
                return False
                
            self.process_cluster_data(ranks_data)
            self.save_cluster_summary(token_address, ranks_data)
            
            return True
            
        except Exception as e:
            self.log.error(f"Error processing token: {e}", exc_info=True)
            return False


def process_token_threaded(token_address: str, chrome_bin_path: str = "", chrome_driver_path: str = "") -> None:
    """Process a single token in a separate thread."""
    driver = None
    thread_id = f"Thread-{os.getpid()}"
    logging.info(f"[{thread_id}] Processing token: {token_address}")
    
    try:
        # Initialize WebDriver
        options = webdriver.ChromeOptions()
        if chrome_bin_path:
            options.binary_location = chrome_bin_path
        
        driver = webdriver.Chrome(
            executable_path=chrome_driver_path if chrome_driver_path else None,
            options=options
        )
        
        # Process the token
        processor = BubblemapsProcessor(driver, thread_id)
        success = processor.process_token(token_address)
        
        if success:
            # Save to processed tokens
            try:
                with open(OPENED_TOKENS_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"{token_address}\n")
            except Exception as e:
                logging.error(f"[{thread_id}] Error saving processed token: {e}")
    
    except Exception as e:
        logging.error(f"[{thread_id}] Error in token processing thread: {e}", exc_info=True)
    
    finally:
        if driver:
            try:
                driver.quit()
                logging.debug(f"[{thread_id}] WebDriver closed")
            except Exception as e:
                logging.error(f"[{thread_id}] Error closing WebDriver: {e}")


def load_processed_tokens() -> Set[str]:
    """Load the set of already processed tokens."""
    if not os.path.exists(OPENED_TOKENS_FILE):
        return set()
    
    try:
        with open(OPENED_TOKENS_FILE, 'r', encoding='utf-8') as f:
            return {line.strip() for line in f if line.strip()}
    except Exception as e:
        logging.error(f"Error loading processed tokens: {e}")
        return set()


def get_new_tokens(csv_file: str = 'sniperx_results_1m.csv') -> List[str]:
    """Get new tokens from the CSV file that haven't been processed yet."""
    if not os.path.exists(csv_file):
        logging.warning(f"CSV file {csv_file} not found")
        return []
    
    processed_tokens = load_processed_tokens()
    new_tokens = []
    
    try:
        with open(csv_file, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                token_address = row.get('tokenAddress') or row.get('address', '').strip()
                if token_address and token_address not in processed_tokens:
                    new_tokens.append(token_address)
                    if len(new_tokens) >= 10:  # Limit to 10 tokens per batch
                        break
    
    except Exception as e:
        logging.error(f"Error reading CSV file: {e}")
    
    return new_tokens
