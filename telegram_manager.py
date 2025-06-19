#!/usr/bin/env python3

import logging
import os
import subprocess
import sys
import signal # For sending signals like SIGINT, SIGSTOP, SIGCONT
import asyncio
import json
from decimal import Decimal
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import time
from datetime import datetime

load_dotenv(dotenv_path='env.txt')

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") # Your specific chat ID to restrict commands
SNIPERX_SCRIPT_NAME = "SniperX V2.py" # Make sure this is in the same directory or provide full path
WALLET_MANAGER_SCRIPT = "wallet_manager.py"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SNIPERX_SCRIPT_PATH = os.path.join(SCRIPT_DIR, SNIPERX_SCRIPT_NAME)
WALLET_MANAGER_PATH = os.path.join(SCRIPT_DIR, WALLET_MANAGER_SCRIPT)

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Global state for SniperX process ---
sniperx_process: subprocess.Popen | None = None
wallet_manager_process: subprocess.Popen | None = None
current_balance = {"sol": Decimal("0"), "usd": Decimal("0")}

# --- Helper to check if user is authorized ---
def is_authorized(update: Update) -> bool:
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set. Bot will respond to anyone.")
        return True # Or False for security
    return update.effective_chat is not None and str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

# --- Command Handlers ---
async def start_command(update: Update, context: CallbackContext) -> None:
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    global sniperx_process, wallet_manager_process
    if sniperx_process and sniperx_process.poll() is None:
        if update.message:
            await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} is already running (PID: {sniperx_process.pid}).")
    else:
        try:
            if not os.path.exists(SNIPERX_SCRIPT_PATH):
                if update.message:
                    await update.message.reply_text(f"Error: {SNIPERX_SCRIPT_NAME} not found at {SNIPERX_SCRIPT_PATH}.")
                return

            # Start wallet manager first
            if update.message:
                await update.message.reply_text("Starting wallet manager...")
            
            # Start the wallet manager without piping stdout/stderr to avoid
            # blocking if the buffers fill up. Output will be inherited by the
            # parent process and written directly to the console or logs.
            wallet_manager_process = subprocess.Popen(
                [sys.executable, WALLET_MANAGER_PATH],
                cwd=SCRIPT_DIR
            )
            
            # Wait a moment for wallet manager to initialize
            await asyncio.sleep(2)
            
            # Check if the wallet manager exited immediately
            if wallet_manager_process.poll() is not None:
                if update.message:
                    await update.message.reply_text("Failed to start wallet manager.")
                return
            
            # Start SniperX
            sniperx_process = subprocess.Popen(
                [sys.executable, SNIPERX_SCRIPT_PATH],
                cwd=SCRIPT_DIR
            )
            
            if update.message:
                await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} started with PID {sniperx_process.pid}.")
            logger.info(f"SniperX V2.py started with PID {sniperx_process.pid} by user {update.effective_user.username if update.effective_user else 'unknown'}")
        except Exception as e:
            if update.message:
                await update.message.reply_text(f"Failed to start {SNIPERX_SCRIPT_NAME}: {e}")
            logger.error(f"Failed to start SniperX: {e}")

async def stop_command(update: Update, context: CallbackContext) -> None:
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    global sniperx_process, wallet_manager_process
    if sniperx_process and sniperx_process.poll() is None:
        try:
            pid_to_stop = sniperx_process.pid
            if os.name == 'nt': # Windows
                 # subprocess.call(['taskkill', '/F', '/T', '/PID', str(sniperx_process.pid)]) # More forceful
                sniperx_process.terminate() # Sends SIGTERM equivalent
            else: # Linux/macOS
                os.kill(sniperx_process.pid, signal.SIGINT) # Send Ctrl+C equivalent for graceful shutdown
            
            sniperx_process.wait(timeout=10) # Wait for process to terminate
            if update.message:
                await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} (PID: {pid_to_stop}) signaled to stop gracefully.")
            logger.info(f"SniperX V2.py (PID: {pid_to_stop}) stopped by user {update.effective_user.username if update.effective_user else 'unknown'}")
        except subprocess.TimeoutExpired:
            if update.message:
                await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} (PID: {pid_to_stop}) did not stop gracefully. Forcing kill.")
            sniperx_process.kill()
            sniperx_process.wait()
            logger.warning(f"SniperX V2.py (PID: {pid_to_stop}) force killed by user {update.effective_user.username if update.effective_user else 'unknown'}")
        except Exception as e:
            if update.message:
                await update.message.reply_text(f"Error stopping {SNIPERX_SCRIPT_NAME}: {e}")
            logger.error(f"Error stopping SniperX: {e}")
        finally:
            sniperx_process = None

    # Stop wallet manager if running
    if wallet_manager_process and wallet_manager_process.poll() is None:
        try:
            if os.name == 'nt':
                wallet_manager_process.terminate()
            else:
                os.kill(wallet_manager_process.pid, signal.SIGINT)
            wallet_manager_process.wait(timeout=5)
        except Exception as e:
            logger.error(f"Error stopping wallet manager: {e}")
            if wallet_manager_process.poll() is None:
                wallet_manager_process.kill()
        finally:
            wallet_manager_process = None

async def status_command(update: Update, context: CallbackContext) -> None:
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return
    global sniperx_process
    if sniperx_process and sniperx_process.poll() is None:
        status = "paused" if sniperx_process.poll() is None else "running"
        if update.message:
            await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} is currently {status} (PID: {sniperx_process.pid}).")
    else:
        if update.message:
            await update.message.reply_text(f"{SNIPERX_SCRIPT_NAME} is not running.")

async def show_menu(update: Update, context: CallbackContext) -> None:
    """Sends a message with inline buttons."""
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    keyboard = [
        [
            InlineKeyboardButton("🚀 Start SniperX", callback_data='start_sniperx'),
        ],
        [
            InlineKeyboardButton("🛑 Stop SniperX", callback_data='stop_sniperx'),
            InlineKeyboardButton("📊 Status", callback_data='status_sniperx'),
        ],
        [
            InlineKeyboardButton("💰 Balance", callback_data='show_balance'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text='SniperX Bot Control Panel:',
                reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(
                'SniperX Bot Control Panel:',
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error showing menu: {e}")
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(
                'SniperX Bot Control Panel:',
                reply_markup=reply_markup
            )

async def read_wallet_balance():
    """Read the current wallet balance from the JSON file."""
    try:
        balance_file_path = os.path.join(SCRIPT_DIR, "wallet_balance.json")
        if os.path.exists(balance_file_path):
            with open(balance_file_path, "r") as f:
                data = json.load(f)
                # Check if the data is recent (within last 5 minutes)
                timestamp = float(data.get("timestamp", 0))
                if time.time() - timestamp < 300:
                    return {
                        "sol": Decimal(data["sol"]),
                        "usd": Decimal(data["usd"]),
                        "timestamp": timestamp
                    }
                else:
                    logger.warning("Balance data is too old")
    except Exception as e:
        logger.error(f"Error reading wallet balance: {e}")
    return {"sol": Decimal("0"), "usd": Decimal("0"), "timestamp": 0}

async def button_callback(update: Update, context: CallbackContext) -> None:
    # Declare global variables at the start of the function
    global current_balance, wallet_manager_process, sniperx_process
    
    query = update.callback_query
    if not query:
        return
        
    await query.answer()

    if not is_authorized(update):
        if query.message:
            await query.edit_message_text(text="You are not authorized for this action.")
        return

    action = query.data
    if not action:
        return
        
    try:
        if action == 'start_sniperx':
            if query.message and query.message.chat and query.from_user:
                update_dict = {
                    'update_id': 0,
                    'message': query.message.to_dict(),
                    'effective_chat': query.message.chat.to_dict(),
                    'effective_user': query.from_user.to_dict()
                }
                pseudo_update = Update.de_json(update_dict, context.bot)
                if pseudo_update:
                    await start_command(pseudo_update, context)
                await show_menu(update, context)
        elif action == 'stop_sniperx':
            if query.message and query.message.chat and query.from_user:
                update_dict = {
                    'update_id': 0,
                    'message': query.message.to_dict(),
                    'effective_chat': query.message.chat.to_dict(),
                    'effective_user': query.from_user.to_dict()
                }
                pseudo_update = Update.de_json(update_dict, context.bot)
                if pseudo_update:
                    await stop_command(pseudo_update, context)
                await show_menu(update, context)
        elif action == 'status_sniperx':
            global sniperx_process
            if sniperx_process and sniperx_process.poll() is None:
                status_text = "running"
                await query.edit_message_text(
                    text=f"{SNIPERX_SCRIPT_NAME} is currently {status_text} (PID: {sniperx_process.pid}).",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')
                    ]])
                )
            else:
                await query.edit_message_text(
                    text=f"{SNIPERX_SCRIPT_NAME} is not running.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')
                    ]])
                )
        elif action == 'show_balance':
            # Update balance from file
            current_balance = await read_wallet_balance()
            
            # Check if SniperX is running
            if not sniperx_process or (hasattr(sniperx_process, 'poll') and sniperx_process.poll() is not None):
                balance_text = "SniperX is not running. Please start SniperX first."
            # Check if wallet manager is running
            elif not wallet_manager_process or wallet_manager_process.poll() is not None:
                # Try to restart wallet manager if SniperX is running but wallet manager isn't
                try:
                    wallet_manager_process = subprocess.Popen(
                        [sys.executable, WALLET_MANAGER_PATH],
                        cwd=SCRIPT_DIR
                    )
                    await asyncio.sleep(2)  # Give it a moment to start
                    if wallet_manager_process.poll() is not None:
                        balance_text = "Failed to start wallet manager. Please restart SniperX."
                    else:
                        current_balance = await read_wallet_balance()
                        if current_balance["sol"] == Decimal("0"):
                            balance_text = "Wallet manager is starting up. Please wait a moment and try again."
                        else:
                            last_update = datetime.fromtimestamp(current_balance["timestamp"]).strftime("%H:%M:%S")
                            balance_text = f"Current Balance:\nSOL: {current_balance['sol']:.6f}\nUSD: ${current_balance['usd']:.2f}\n\nLast Update: {last_update}"
                except Exception as e:
                    logger.error(f"Error restarting wallet manager: {e}")
                    balance_text = "Error starting wallet manager. Please restart SniperX."
            elif current_balance["sol"] == Decimal("0"):
                balance_text = "Waiting for wallet balance update...\nPlease try again in a few seconds."
            else:
                last_update = datetime.fromtimestamp(current_balance["timestamp"]).strftime("%H:%M:%S")
                balance_text = f"Current Balance:\nSOL: {current_balance['sol']:.6f}\nUSD: ${current_balance['usd']:.2f}\n\nLast Update: {last_update}"
            
            # Always try to edit the existing message first
            try:
                await query.edit_message_text(
                    text=balance_text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Refresh", callback_data='show_balance'),
                        InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')
                    ]])
                )
            except Exception as e:
                logger.error(f"Error updating balance message: {e}")
                # If editing fails, try to delete the old message and send a new one
                try:
                    if query.message:
                        await query.message.delete()
                        await query.message.reply_text(
                            text=balance_text,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🔄 Refresh", callback_data='show_balance'),
                                InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')
                            ]])
                        )
                except Exception as delete_error:
                    logger.error(f"Error handling message update: {delete_error}")
        elif action == 'back_to_menu':
            await show_menu(update, context)
    except Exception as e:
        logger.error(f"Error in button callback: {e}")
        if query.message:
            try:
                await query.message.reply_text(
                    "An error occurred. Please try /menu again.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')
                    ]])
                )
            except Exception as reply_error:
                logger.error(f"Error sending error message: {reply_error}")

async def unknown_command(update: Update, context: CallbackContext) -> None:
    if not is_authorized(update): return
    if update.message:
        await update.message.reply_text("Sorry, I didn't understand that command.")

def main_telegram_bot() -> None:
    """Start the bot."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not found in environment variables. Bot cannot start.")
        return
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set. Bot will respond to commands from any user.")

    # Create a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start_command)) # Start SniperX
    application.add_handler(CommandHandler("stop", stop_command))   # Stop SniperX
    application.add_handler(CommandHandler("status", status_command)) # Get SniperX status
    application.add_handler(CommandHandler("menu", show_menu)) # Show control menu

    # CallbackQueryHandler for inline buttons
    application.add_handler(CallbackQueryHandler(button_callback))

    # Handler for unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Telegram Manager Bot started. Send /menu to interact.")
    
    # Send a startup message to the designated chat ID if configured
    if TELEGRAM_CHAT_ID:
        try:
            loop.run_until_complete(application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="SniperX Control Bot is online! Send /menu to see options."))
        except Exception as e:
            logger.error(f"Could not send startup message to TELEGRAM_CHAT_ID {TELEGRAM_CHAT_ID}: {e}")

    try:
        # Start the bot
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error in polling: {e}")
    finally:
        # Cleanup when bot stops
        global sniperx_process, wallet_manager_process
        if sniperx_process and sniperx_process.poll() is None:
            logger.info("Telegram bot shutting down. Attempting to stop SniperX V2.py...")
            if os.name == 'nt':
                sniperx_process.terminate()
            else:
                os.kill(sniperx_process.pid, signal.SIGINT)
            try:
                sniperx_process.wait(5)
            except subprocess.TimeoutExpired:
                sniperx_process.kill()
            logger.info("SniperX V2.py process terminated during bot shutdown.")
        
        if wallet_manager_process and wallet_manager_process.poll() is None:
            logger.info("Stopping wallet manager...")
            if os.name == 'nt':
                wallet_manager_process.terminate()
            else:
                os.kill(wallet_manager_process.pid, signal.SIGINT)
            try:
                wallet_manager_process.wait(5)
            except subprocess.TimeoutExpired:
                wallet_manager_process.kill()
            logger.info("Wallet manager terminated during bot shutdown.")
        
        loop.close()


if __name__ == "__main__":
    try:
        main_telegram_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")