#!/bin/bash
cd /home/tk578/.openclaw/workspace/x-scraper
./venv/bin/python3 run_scheduler.py >> scheduler.log 2>&1
