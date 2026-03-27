#!/bin/bash
# CISSP Study App — macOS Launcher
# Double-click this file to start the app.

cd "$(dirname "$0")"

echo ""
echo "=========================================="
echo "  🛡️  CISSP Study App — Starting..."
echo "=========================================="
echo ""

# Check Python3
if ! command -v python3 &> /dev/null; then
  echo "❌ Python3 not found. Please install Python from https://python.org"
  read -p "Press Enter to close."
  exit 1
fi

echo "  ✅ Python3 found: $(python3 --version)"
echo "  📂 Working folder: $(pwd)"
echo "  🚀 Starting server on http://localhost:5432 ..."
echo ""
echo "  Your browser will open automatically."
echo "  To stop: press Ctrl+C in this window."
echo ""

python3 cissp_server.py
