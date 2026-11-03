"""The app's pages, in navigation order. One file per page under pages/."""

PAGES = [
    {"path": "pages/overview.py", "title": "Overview", "icon": ":material/dashboard:", "group": "main"},
    {"path": "pages/incidents.py", "title": "Incidents", "icon": ":material/notifications_active:", "group": "main"},
    {"path": "pages/tests_and_anomalies.py", "title": "Tests & anomalies", "icon": ":material/rule:", "group": "main"},
    {"path": "pages/runs.py", "title": "Runs", "icon": ":material/play_circle:", "group": "main"},
    {"path": "pages/schedules.py", "title": "Schedules & freshness", "icon": ":material/schedule:", "group": "main"},
    {"path": "pages/schema_changes.py", "title": "Schema changes", "icon": ":material/difference:", "group": "main"},
    {"path": "pages/cost.py", "title": "Cost", "icon": ":material/payments:", "group": "main"},
    {"path": "pages/lineage.py", "title": "Lineage", "icon": ":material/account_tree:", "group": "explore"},
    {"path": "pages/node.py", "title": "Models & sources", "icon": ":material/table_view:", "group": "explore"},
    {"path": "pages/settings.py", "title": "Settings & about", "icon": ":material/settings:", "group": "bottom"},
]

# st.navigation section headers: the group name maps to the header shown in the sidebar.
SECTIONS = {"main": "", "explore": "Explore", "bottom": " "}
NODE_PAGE = "pages/node.py"
