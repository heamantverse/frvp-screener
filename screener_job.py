def fetch_52_week_data(smart_api, token):
    end = datetime.now()
    start = end - timedelta(weeks=WEEKS_LOOKBACK)
    all_chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
        params = {
            "exchange": "NSE", "symboltoken": token, "interval": INTERVAL,
            "fromdate": chunk_start.strftime("%Y-%m-%d 09:15"),
            "todate": chunk_end.strftime("%Y-%m-%d 15:30"),
        }
        for attempt in range(3):
            try:
                response = smart_api.getCandleData(params)
                if response.get("status"):
                    if response.get("data"):
                        all_chunks.extend(response["data"])
                    break
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        chunk_start = chunk_end
        time.sleep(API_DELAY_SEC)

    if not all_chunks:
        return pd.DataFrame()
    df = pd.DataFrame(all_chunks, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    # chunk boundaries can repeat a candle; remove duplicates
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df
