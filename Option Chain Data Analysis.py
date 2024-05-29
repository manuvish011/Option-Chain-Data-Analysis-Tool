from flask import Flask, render_template_string
import requests
import json
import math
import time
import threading

app = Flask(__name__)

# Method to get nearest strikes
def round_nearest(x, num=50): 
    return int(math.ceil(float(x)/num)*num)

def nearest_strike_bnf(x): 
    return round_nearest(x, 100)

def nearest_strike_nf(x): 
    return round_nearest(x, 50)

# URLs for fetching Data
url_oc = 'https://www.nseindia.com/option-chain'
url_bnf = 'https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY'
url_nf = 'https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY'
url_indices = 'https://www.nseindia.com/api/allIndices'

# Headers
headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
           'accept-language': 'en,gu;q=0.9,hi;q=0.8',
           'accept-encoding': 'gzip, deflate, br'}

sess = requests.Session()
cookies = dict()

# Local methods
def set_cookie():
    request = sess.get(url_oc, headers=headers, timeout=5)
    cookies.update(request.cookies)

def get_data(url):
    set_cookie()
    response = sess.get(url, headers=headers, timeout=5, cookies=cookies)
    if response.status_code == 401:
        set_cookie()
        response = sess.get(url, headers=headers, timeout=5, cookies=cookies)
    if response.status_code == 200:
        return response.text
    return ''

def set_header():
    global bnf_ul
    global nf_ul
    global bnf_nearest
    global nf_nearest
    response_text = get_data(url_indices)
    data = json.loads(response_text)
    for index in data['data']:
        if index['index'] == 'NIFTY 50':
            nf_ul = index['last']
        if index['index'] == 'NIFTY BANK':
            bnf_ul = index['last']
    bnf_nearest = nearest_strike_bnf(bnf_ul)
    nf_nearest = nearest_strike_nf(nf_ul)

# Fetching CE and PE data based on Nearest Expiry Date
def get_oi_data(num, step, nearest, url):
    strike = nearest - (step * num)
    start_strike = nearest - (step * num)
    response_text = get_data(url)
    data = json.loads(response_text)
    currExpiryDate = data['records']['expiryDates'][0]
    oi_data = []
    for item in data['records']['data']:
        if item['expiryDate'] == currExpiryDate:
            if item['strikePrice'] == strike and item['strikePrice'] < start_strike + (step * num * 2):
                oi_data.append({
                    'expiryDate': data['records']['expiryDates'][0],
                    'strikePrice': item['strikePrice'],
                    'CE_OI': item['CE']['openInterest'],
                    'PE_OI': item['PE']['openInterest']
                })
                strike = strike + step
    return oi_data

# Finding highest Open Interest of People's in CE based on CE data
def highest_oi_CE(num, step, nearest, url):
    strike = nearest - (step * num)
    start_strike = nearest - (step * num)
    response_text = get_data(url)
    data = json.loads(response_text)
    currExpiryDate = data['records']['expiryDates'][0]
    max_oi = 0
    max_oi_strike = 0
    for item in data['records']['data']:
        if item['expiryDate'] == currExpiryDate:
            if item['strikePrice'] == strike and item['strikePrice'] < start_strike + (step * num * 2):
                if item['CE']['openInterest'] > max_oi:
                    max_oi = item['CE']['openInterest']
                    max_oi_strike = item['strikePrice']
                strike = strike + step
    return max_oi_strike

# Finding highest Open Interest of People's in PE based on PE data
def highest_oi_PE(num, step, nearest, url):
    strike = nearest - (step * num)
    start_strike = nearest - (step * num)
    response_text = get_data(url)
    data = json.loads(response_text)
    currExpiryDate = data['records']['expiryDates'][0]
    max_oi = 0
    max_oi_strike = 0
    for item in data['records']['data']:
        if item['expiryDate'] == currExpiryDate:
            if item['strikePrice'] == strike and item['strikePrice'] < start_strike + (step * num * 2):
                if item['PE']['openInterest'] > max_oi:
                    max_oi = item['PE']['openInterest']
                    max_oi_strike = item['strikePrice']
                strike = strike + step
    return max_oi_strike

# Determine buy/sell signals based on OI data
def determine_signals(num, step, nearest, url):
    response_text = get_data(url)
    data = json.loads(response_text)
    currExpiryDate = data['records']['expiryDates'][0]
    signals = []

    strike = nearest - (step * num)
    start_strike = nearest - (step * num)
    for item in data['records']['data']:
        if item['expiryDate'] == currExpiryDate:
            if item['strikePrice'] == strike and item['strikePrice'] < start_strike + (step * num * 2):
                ce_oi = item['CE']['openInterest']
                pe_oi = item['PE']['openInterest']
                if ce_oi > 1.5 * pe_oi:  # Buy signal if CE OI is significantly higher
                    signals.append((item['strikePrice'], 'BUY'))
                elif pe_oi > 1.5 * ce_oi:  # Sell signal if PE OI is significantly higher
                    signals.append((item['strikePrice'], 'SELL'))
                strike = strike + step

    return signals

# Data to be shared across threads
data_dict = {}

def refresh_data():
    global data_dict
    global nf_nearest
    global bnf_nearest
    while True:
        set_header()

        nf_oi_data = get_oi_data(10, 50, nf_nearest, url_nf)
        bnf_oi_data = get_oi_data(10, 100, bnf_nearest, url_bnf)

        nf_highestoi_CE = highest_oi_CE(10, 50, nf_nearest, url_nf)
        nf_highestoi_PE = highest_oi_PE(10, 50, nf_nearest, url_nf)
        bnf_highestoi_CE = highest_oi_CE(10, 100, bnf_nearest, url_bnf)
        bnf_highestoi_PE = highest_oi_PE(10, 100, bnf_nearest, url_bnf)

        nifty_signals = determine_signals(10, 50, nf_nearest, url_nf)
        bank_nifty_signals = determine_signals(10, 100, bnf_nearest, url_bnf)

        data_dict = {
            'nf_oi_data': nf_oi_data,
            'bnf_oi_data': bnf_oi_data,
            'nf_highestoi_CE': nf_highestoi_CE,
            'nf_highestoi_PE': nf_highestoi_PE,
            'bnf_highestoi_CE': bnf_highestoi_CE,
            'bnf_highestoi_PE': bnf_highestoi_PE,
            'nifty_signals': nifty_signals,
            'bank_nifty_signals': bank_nifty_signals
        }

        time.sleep(300)

@app.route('/')
def index():
    global data_dict
    data = data_dict
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Option Chain Data</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #f8f9fa;
                padding: 20px;
            }
            .container {
                max-width: 1200px;
                margin: auto;
            }
            .table {
                width: 100%;
                margin-bottom: 20px;
                background-color: #fff;
                border: 1px solid #dee2e6;
                border-radius: 0.25rem;
            }
            th, td {
                padding: 10px;
                text-align: center;
                vertical-align: middle;
            }
            th {
                background-color: #007bff;
                color: #fff;
                border-bottom: 2px solid #dee2e6;
            }
            .support, .resistance {
                font-weight: bold;
                margin-top: 20px;
            }
            .buy-sell {
                margin-top: 20px;
                text-align: center;
            }
            .buy {
                color: green;
                font-weight: bold;
            }
            .sell {
                color: red;
                font-weight: bold;
            }
            .header {
                background-color: #343a40;
                color: #fff;
                padding: 15px 0;
                text-align: center;
                margin-bottom: 30px;
            }
            .header h1 {
                margin: 0;
                font-size: 2.5rem;
            }
            .sub-header {
                margin-bottom: 20px;
            }
            .sub-header h2 {
                background-color: #6c757d;
                color: #fff;
                padding: 10px;
                border-radius: 0.25rem;
            }
            #refresh-timer {
                text-align: center;
                font-weight: bold;
                margin-bottom: 20px;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Option Chain Data</h1>
        </div>
        <div id="refresh-timer">Refreshing data in <span id="countdown"></span> seconds...</div>
        <div class="container">
            <div class="row">
                <div class="col-md-6">
                    <div class="sub-header">
                        <h2>Nifty</h2>
                    </div>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>Expiry Date</th>
                                <th>Strike Price</th>
                                <th>CE OI</th>
                                <th>PE OI</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in data['nf_oi_data'] %}
                            <tr>
                                <td>{{ item['expiryDate'] }}</td>
                                <td>{{ item['strikePrice'] }}</td>
                                <td>{{ item['CE_OI'] }}</td>
                                <td>{{ item['PE_OI'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <div class="support">Major Support in Nifty: {{ data['nf_highestoi_CE'] }}</div>
                    <div class="resistance">Major Resistance in Nifty: {{ data['nf_highestoi_PE'] }}</div>
                </div>
                <div class="col-md-6">
                    <div class="sub-header">
                        <h2>Bank Nifty</h2>
                    </div>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>Expiry Date</th>
                                <th>Strike Price</th>
                                <th>CE OI</th>
                                <th>PE OI</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in data['bnf_oi_data'] %}
                            <tr>
                                <td>{{ item['expiryDate'] }}</td>
                                <td>{{ item['strikePrice'] }}</td>
                                <td>{{ item['CE_OI'] }}</td>
                                <td>{{ item['PE_OI'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    <div class="support">Major Support in Bank Nifty: {{ data['bnf_highestoi_CE'] }}</div>
                    <div class="resistance">Major Resistance in Bank Nifty: {{ data['bnf_highestoi_PE'] }}</div>
                </div>
            </div>
            <div class="row">
                <div class="col-md-6">
                    <div class="sub-header">
                        <h2>Nifty Buy/Sell Signals</h2>
                    </div>
                    <div class="buy-sell">
                        {% for signal in data['nifty_signals'] %}
                        <p class="{% if signal[1] == 'BUY' %}buy{% else %}sell{% endif %}">Strike Price: {{ signal[0] }} ({{ signal[1] }})</p>
                        {% endfor %}
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="sub-header">
                        <h2>Bank Nifty Buy/Sell Signals</h2>
                    </div>
                    <div class="buy-sell">
                        {% for signal in data['bank_nifty_signals'] %}
                        <p class="{% if signal[1] == 'BUY' %}buy{% else %}sell{% endif %}">Strike Price: {{ signal[0] }} ({{ signal[1] }})</p>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
        <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.9.2/dist/umd/popper.min.js"></script>
        <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.min.js"></script>
        <script>
            // Function to update countdown timer
            function updateCountdown(seconds) {
                var countdownElement = document.getElementById('countdown');
                countdownElement.textContent = seconds;
            }

            // Function to start countdown
            function startCountdown(seconds) {
                updateCountdown(seconds);
                var interval = setInterval(function() {
                    seconds--;
                    updateCountdown(seconds);
                    if (seconds <= 0) {
                        clearInterval(interval);
                        location.reload(); // Refresh the page after countdown
                    }
                }, 1000);
            }

            // Start countdown on page load
            document.addEventListener('DOMContentLoaded', function() {
                startCountdown(300); // Start countdown for 5 minutes (300 seconds)
            });
        </script>
    </body>
    </html>
    ''', data=data)

if __name__ == '__main__':
    threading.Thread(target=refresh_data).start()
    app.run(debug=True, use_reloader=False)