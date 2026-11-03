.PHONY: app demo-data

# Run the app on your laptop (docs/app.md). Set ICECORE_VIEWS_SCHEMA and a Snowflake login first.
app:
	scripts/run_app_locally.sh

# Fill a persistent demo schema so every page has content (docs/app.md).
demo-data:
	scripts/demo_data.sh
