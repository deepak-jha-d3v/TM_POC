"""
build_ui.py
-----------
Injects output/alerts_for_ui.json and the rule catalog into ui_template.html
to produce the final output/case_review_ui.html -- a standalone file, no
server needed.
"""

import json

from rules_engine import RULE_NAMES, RULE_WEIGHTS

with open("output/alerts_for_ui.json") as f:
    alerts = json.load(f)

rule_catalog = [{"rule_id": rid, "name": name, "weight": RULE_WEIGHTS[rid]}
                 for rid, name in RULE_NAMES.items()]

with open("ui_template.html") as f:
    template = f.read()

html = template.replace("__ALERTS_JSON__", json.dumps(alerts))
html = html.replace("__RULE_CATALOG_JSON__", json.dumps(rule_catalog))

with open("output/case_review_ui.html", "w") as f:
    f.write(html)

print(f"Built output/case_review_ui.html with {len(alerts)} alerts and {len(rule_catalog)} rules.")
