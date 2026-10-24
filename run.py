"""
Simple script to run the Dynamic AI Chatbot locally.

Reads settings from environment variables / a .env file (see .env.example).
"""

import sys
import time
import threading
import webbrowser

from config import ConfigError, load_config
from db import DatabaseError
from server import create_app


def main():
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"❌ Configuration error: {exc}")
        sys.exit(2)

    url = f"http://{config.host}:{config.port}"
    try:
        app = create_app(config)
    except DatabaseError as exc:
        print(f"❌ Storage error: {exc}")
        sys.exit(2)

    print("🤖 Starting Dynamic AI Chatbot...")
    print(f"📡 Server will start on {url}")
    if not config.ai_configured:
        print("⚠️  GEMINI_API_KEY and/or GEMINI_MODEL is not set - AI answers will fail with HTTP 503.")
        print("    Copy .env.example to .env and fill it in (see README).")
    print("🌐 Opening browser in 3 seconds...")

    def run_server():
        app.run(debug=False, host=config.host, port=config.port, use_reloader=False)

    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()

    time.sleep(3)
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("✅ Chatbot is running!")
    print(f"💬 Open {url} in your browser to start chatting")
    print("⏹️  Press Ctrl+C to stop the server")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down the chatbot...")
        sys.exit(0)


if __name__ == "__main__":
    main()
