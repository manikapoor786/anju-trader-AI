"""Telegram delivery + interactive reply handler.

Outbound (push):
    digest.py       Morning digest with signals + reasoning
    alerts.py       Intraday SL/target/anomaly alerts
    weekly_report.py  Sunday weekly report + critic proposals
    ab_compare.py   anju-trader vs anju-trader-AI comparison

Inbound (pull):
    webhook.py      Receives Telegram callbacks, triggers GH Actions workflows
    commands.py     Parses /scan, /review, /book, /approve_<id>, /reject_<id>

Phase 0 has digest + alerts as text only.
Phase 3 adds the webhook for /approve_<id> /reject_<id> interactivity.
"""
