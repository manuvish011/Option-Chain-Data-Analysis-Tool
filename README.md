## Option Chain Data Analysis Tool

This Flask application provides a real-time analysis of the Indian stock market's option chain data for Nifty and Bank Nifty. It fetches data from the NSE India website, calculates key indicators, and displays them in an intuitive web interface.

**Features:**

- **Real-time data fetching:** Continuously retrieves and updates data from the NSE India website.
- **Open Interest Analysis:** Displays the open interest (OI) for Calls and Puts across various strike prices for the nearest expiry date.
- **Major Support and Resistance:** Identifies the strike prices with the highest OI for both Calls and Puts, representing potential support and resistance levels.
- **Confidence Scoring:** Combines OI, change in OI, traded volume, IV skew, and price action into a bullish, bearish, or neutral bias score.
- **Price Action Confirmation:** Uses selectable 1m, 3m, or 5m EMA alignment, VWAP, previous-day range, and intraday range position as confirmation filters.
- **Trade Idea:** Shows Buy Call, Buy Put, or Wait / No Trade with a suggested strike and entry zone.
- **Risk Context:** Shows support, resistance, target zone, and invalidation levels for each index.
- **User-friendly interface:** Presents only dashboard cards and reason lists while keeping raw option-chain calculations in the background.
- **Automatic Refresh:** Refreshes the data every 5 minutes to ensure up-to-date information.

**Usage:**

1. **Prerequisites:**
    - Python 3.x
    - Flask library: `pip install Flask`
    - requests library: `pip install requests`
2. **Run the app:**
    - Navigate to the directory containing the code.
    - Install dependencies with `pip install -r requirements.txt`.
    - Run the command `python OptionChainDataAnalysis.py`.
    - You can also use `flask --app OptionChainDataAnalysis run`; the data refresh thread starts when the app is imported.
3. **Access the web interface:**
    - Open your web browser and go to `http://127.0.0.1:5000/`.

**Note:**

- This application relies on the NSE India API and may be subject to changes in the API structure or availability.
- The application does not provide financial advice and should not be used for making investment decisions.
- The confidence score is not a guaranteed prediction or a buy/sell command. It is a decision-support indicator that must be confirmed with price action and risk management.


This project is a starting point for building a more sophisticated option chain analysis tool. Feel free to explore and extend it based on your needs. 
