"""
Main script for processing tokens through Bubblemaps.
"""
import os
import sys
import time
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from bubblemaps_processor import BubblemapsProcessor, process_token_threaded, get_new_tokens

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('token_processor.log'),
        logging.StreamHandler()
    ]
)

def setup_chrome_driver(chrome_bin_path: str = "", chrome_driver_path: str = "") -> webdriver.Chrome:
    """Set up and return a Chrome WebDriver instance."""
    options = webdriver.ChromeOptions()
    
    # Set Chrome binary location if provided
    if chrome_bin_path:
        options.binary_location = chrome_bin_path
    
    # Add common options
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    # Initialize the WebDriver
    if chrome_driver_path:
        service = Service(executable_path=chrome_driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)
    
    return driver

def process_token_batch(tokens: List[str], max_workers: int = 3) -> None:
    """Process a batch of tokens using ThreadPoolExecutor."""
    if not tokens:
        logging.info("No tokens to process")
        return
    
    logging.info(f"Processing {len(tokens)} tokens with {max_workers} workers")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_token = {
            executor.submit(process_token_threaded, token): token 
            for token in tokens
        }
        
        # Process results as they complete
        for future in as_completed(future_to_token):
            token = future_to_token[future]
            try:
                future.result()
                logging.info(f"Completed processing token: {token}")
            except Exception as e:
                logging.error(f"Error processing token {token}: {e}", exc_info=True)

def monitor_and_process(csv_file: str, check_interval: int = 60, max_tokens: int = 10) -> None:
    """Monitor the CSV file for new tokens and process them."""
    logging.info(f"Starting to monitor {csv_file} for new tokens...")
    
    processed_tokens = set()
    
    while True:
        try:
            # Get new tokens from CSV
            new_tokens = get_new_tokens(csv_file)
            
            # Filter out already processed tokens
            tokens_to_process = [
                token for token in new_tokens 
                if token not in processed_tokens
            ][:max_tokens]
            
            if tokens_to_process:
                logging.info(f"Found {len(tokens_to_process)} new tokens to process")
                process_token_batch(tokens_to_process)
                processed_tokens.update(tokens_to_process)
            else:
                logging.debug("No new tokens found")
            
            # Wait before next check
            time.sleep(check_interval)
            
        except KeyboardInterrupt:
            logging.info("Shutting down...")
            break
        except Exception as e:
            logging.error(f"Error in monitoring loop: {e}", exc_info=True)
            time.sleep(check_interval)

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description='Process tokens through Bubblemaps.')
    parser.add_argument('--csv', default='sniperx_results_1m.csv',
                      help='Path to the CSV file containing tokens')
    parser.add_argument('--chrome-bin', default='',
                      help='Path to Chrome/Chromium binary (optional)')
    parser.add_argument('--chrome-driver', default='',
                      help='Path to ChromeDriver (optional)')
    parser.add_argument('--max-tokens', type=int, default=10,
                      help='Maximum number of tokens to process per batch')
    parser.add_argument('--check-interval', type=int, default=60,
                      help='Interval in seconds between checks for new tokens')
    parser.add_argument('--workers', type=int, default=3,
                      help='Number of concurrent workers')
    
    args = parser.parse_args()
    
    # Set environment variables for Chrome paths if provided
    if args.chrome_bin:
        os.environ['GOOGLE_CHROME_BIN'] = args.chrome_bin
    if args.chrome_driver:
        os.environ['CHROMEDRIVER_PATH'] = args.chrome_driver
    
    logging.info("Starting token processor...")
    logging.info(f"Monitoring file: {args.csv}")
    logging.info(f"Max tokens per batch: {args.max_tokens}")
    logging.info(f"Check interval: {args.check_interval}s")
    logging.info(f"Number of workers: {args.workers}")
    
    try:
        monitor_and_process(
            csv_file=args.csv,
            check_interval=args.check_interval,
            max_tokens=args.max_tokens
        )
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
