from db_logger import create_tables, log_to_db
import sqlite3

# Initialize tables
create_tables()

# Insert mock rows
data1 = {
    'Address': 'ABC123',
    'Name': 'TokenA',
    'Price USD': '0.01'
}
log_to_db('sniperx_results_1m', data1)

trade_row = {
    'timestamp': '2024-01-01 00:00:00',
    'token_name': 'TokenA',
    'mint_address': 'ABC123',
    'reason': 'test',
    'buy_price': '1',
    'sell_price': '2',
    'gain_loss_pct': '100',
    'result': 'PROFIT'
}
log_to_db('trades', trade_row)

conn = sqlite3.connect('sniperx_log.db')
for table in ('sniperx_results_1m', 'trades'):
    rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
    print(table, rows)
conn.close()
