import yfinance as yf
import pandas as pd
import numpy as np
import io
import requests
import logging
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress noisy yfinance logs
yf_logger = logging.getLogger('yfinance')
yf_logger.setLevel(logging.CRITICAL)
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

def send_telegram_alert(message):
    """Sends real-time breakout signals to your phone via Telegram Bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ Telegram credentials not found. Printing alert to console:")
        print(message)
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"⚠️ Failed to send Telegram alert: {e}")

def download_ticker_data(ticker, start_date, end_date):
    try:
        df = yf.download(ticker, start=start_date, end=end_date, progress=False)
        if df.empty or len(df) < 50:
            return ticker, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df['Turnover'] = df['Close'] * df['Volume']
        if df['Turnover'].tail(20).mean() < 10000000:
            return ticker, None
            
        df['EMA_10'] = df['Close'].ewm(span=10, adjust=False).mean()
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['Range_Pct'] = (df['High'] - df['Low']) / df['Close']
        df['Vol_Avg'] = df['Volume'].rolling(window=10).mean()
        return ticker, df
    except:
        return ticker, None

def run_cloud_scanner():
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=120)).strftime('%Y-%m-%d')
    end_date = pd.Timestamp.today().strftime('%Y-%m-%d')
    
    print(f"📥 Fetching NSE stock symbol directory for cloud scan ({end_date})...")
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(url, headers=headers)
    df_nse = pd.read_csv(io.StringIO(res.text))
    tickers = [str(symbol).strip() + ".NS" for symbol in df_nse['SYMBOL'].tolist()]
    
    print(f"🚀 Downloading data for {len(tickers)} stocks in cloud worker...")
    all_stock_dfs = {}
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(download_ticker_data, ticker, start_date, end_date): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker, df = future.result()
            if df is not None:
                all_stock_dfs[ticker] = df
                
    print(f"✅ Loaded {len(all_stock_dfs)} liquid stocks. Evaluating today's bar...")
    today_signals = []
    
    for ticker, df in all_stock_dfs.items():
        if len(df) < 25:
            continue
        i = len(df) - 1  # Evaluate the most recent trading day
        row = df.iloc[i]
        prev_rows = df.iloc[i-5:i-1]
        
        is_consolidation = prev_rows['Range_Pct'].mean() < 0.025
        trend_aligned = row['EMA_10'] > row['EMA_20']
        expansion = (row['Close'] > row['Open']) and (row['Close'] > df['High'].iloc[i-1])
        buffer_check = row['Low'] > (row['EMA_10'] * 1.01)
        volume_check = row['Volume'] > (row['Vol_Avg'] * 1.25) if row['Vol_Avg'] > 0 else False
        
        day_high, day_low, day_close = row['High'], row['Low'], row['Close']
        close_location = (day_close - day_low) / (day_high - day_low) if day_high > day_low else 0
        closing_range_check = close_location >= 0.70
        
        sl_risk_pct = (day_close - day_low) / day_close
        sl_risk_check = sl_risk_pct <= 0.035
        
        if is_consolidation and trend_aligned and expansion and buffer_check and volume_check and closing_range_check and sl_risk_check:
            score = (
                (row['Volume'] / row['Vol_Avg'] * 0.4) + 
                ((1 / (prev_rows['Range_Pct'].mean() + 1e-6)) * 0.4) + 
                (close_location * 0.2)
            )
            today_signals.append({
                'ticker': ticker,
                'price': day_close,
                'stop_loss': day_low,
                'score': score
            })
            
    if today_signals:
        df_signals = pd.DataFrame(today_signals).sort_values(by='score', ascending=False).head(2)
        alert_msg = f"🚨 *SWING BREAKOUT ALERTS* ({end_date})\n\n"
        for _, sig in df_signals.iterrows():
            alert_msg += f"• *{sig['ticker']}*\n  Price: ₹{sig['price']:,.2f}\n  Stop-Loss: ₹{sig['stop_loss']:,.2f}\n  Score: {sig['score']:.2f}\n\n"
        send_telegram_alert(alert_msg)
    else:
        print("📭 No qualifying breakout signals detected for today.")

if __name__ == "__main__":
    run_cloud_scanner()
