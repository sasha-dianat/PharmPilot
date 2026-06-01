#!/usr/bin/env bash
# Stop all PharmPilot local processes cleanly
echo "Stopping PharmPilot services..."
pkill -f "uvicorn services.platform.main" 2>/dev/null && echo "  ✓ API stopped"       || echo "  – API was not running"
pkill -f "vite.*3001"                      2>/dev/null && echo "  ✓ Frontend stopped"  || echo "  – Frontend was not running"
pkill -f "celery.*pharmpilot"              2>/dev/null && echo "  ✓ Celery stopped"    || echo "  – Celery was not running"
pkill -f "qdrant"                          2>/dev/null && echo "  ✓ Qdrant stopped"    || echo "  – Qdrant was not running"
echo "Done."
