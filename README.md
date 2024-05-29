## Option Chain Data Analysis Tool

This Flask application provides a real-time analysis of the Indian stock market's option chain data for Nifty and Bank Nifty. It fetches data from the NSE India website, calculates key indicators, and displays them in an intuitive web interface.

**Features:**

- **Real-time data fetching:** Continuously retrieves and updates data from the NSE India website.
- **Open Interest Analysis:** Displays the open interest (OI) for Calls and Puts across various strike prices for the nearest expiry date.
- **Major Support and Resistance:** Identifies the strike prices with the highest OI for both Calls and Puts, representing potential support and resistance levels.
- **Buy/Sell Signals:** Generates buy/sell signals based on the relative open interest of Calls and Puts. 
- **User-friendly interface:** Presents data in a clear and concise format using HTML tables and Bootstrap styling.
- **Automatic Refresh:** Refreshes the data every 5 minutes to ensure up-to-date information.

**Usage:**

1. **Prerequisites:**
    - Python 3.x
    - Flask library: `pip install Flask`
    - requests library: `pip install requests`
    - json library: (already included in Python)
2. **Run the app:**
    - Navigate to the directory containing the code.
    - Run the command `flask run`.
3. **Access the web interface:**
    - Open your web browser and go to `http://127.0.0.1:5000/`.

**Note:**

- This application relies on the NSE India API and may be subject to changes in the API structure or availability.
- The application does not provide financial advice and should not be used for making investment decisions.
- The buy/sell signals are based on a simple analysis of open interest data and should be considered with caution.


This project is a starting point for building a more sophisticated option chain analysis tool. Feel free to explore and extend it based on your needs. 
