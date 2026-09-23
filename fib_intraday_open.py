def main():
    now = datetime.now(IST)
    print(f"Running at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")

    smart_api = login()
    print("Login successful")

    prev_day = get_previous_trading_day()
    print(f"Previous trading day: {prev_day}")

    results = []
    for name, token in [("NIFTY", NIFTY_TOKEN), ("BANKNIFTY", BANKNIFTY_TOKEN)]:
        print(f"\nProcessing {name}...")
        res = process_index(smart_api, name, token, prev_day)
        if res:
            results.append(res)
            print(f"{name} → Success")
        else:
            print(f"{name} → Failed (no data)")

    if not results:
        send_telegram("❌ Fib Open Alert: No data")
        print("No results generated")
        return

    # ... rest of the code same
