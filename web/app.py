"""Flask dashboard server: AI Pipeline intel + Live Market on one page."""
import os

from flask import Flask, jsonify, render_template, request

from utils.db import get_connection
from web import data_access, market

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/recommendations")
def api_recommendations():
    try:
        conn = get_connection()
        try:
            recs = data_access.get_approved_recommendations(conn)
        finally:
            conn.close()
        return jsonify({"success": True, "recommendations": recs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/intel")
def api_intel():
    try:
        conn = get_connection()
        try:
            payload = {
                "alerts": data_access.get_alerts(conn),
                "trends": data_access.get_trends(conn),
                "earnings": data_access.get_earnings(conn),
            }
        finally:
            conn.close()
        return jsonify({"success": True, **payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market")
def api_market():
    try:
        return jsonify({"success": True, "watchlist": market.get_watchlist()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stock/<symbol>")
def api_stock(symbol):
    period = request.args.get("period", "1m")
    try:
        detail = market.get_stock_detail(symbol, period)
        conn = get_connection()
        try:
            detail["recommendation"] = data_access.get_latest_recommendation(
                conn, symbol.upper()
            )
        finally:
            conn.close()
        return jsonify({"success": True, **detail})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
